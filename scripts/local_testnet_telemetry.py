"""Host-only read-only collector. The web Console never receives a runtime socket or node keys."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from network_metrics import aggregate,namespace_counters


def command(*args):
    return subprocess.check_output(args, text=True, timeout=20)


def read(path, default=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default


def tail(path, count=30):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size-128*1024)); lines=stream.read().splitlines()
        values=[]
        for line in lines[-count:]:
            try: values.append(json.loads(line))
            except ValueError: pass
        return values
    except OSError: return []


def collect(root, run):
    ids=command('podman','ps','-aq','--filter','label=org.hypertrix.oracle-training='+run).split()
    ids+=command('podman','ps','-aq','--filter','label=org.hypertrix.seed').split()
    ids+=command('podman','ps','-aq','--filter','label=org.hypertrix.recovery').split()
    ids+=command('podman','ps','-aq','--filter','label=org.hypertrix.lab-role').split()
    ids=list(dict.fromkeys(ids))
    containers=json.loads(command('podman','inspect',*ids)) if ids else []
    current_recovery=max(((c['Config'].get('Labels') or {}).get('org.hypertrix.recovery','') for c in containers),default='')
    alive=[c['Name'] for c in containers if c['State']['Running']]; stats={}; errors=[]
    if alive:
        try:
            rows=json.loads(command('podman','stats','--no-stream','--format','json',*alive))
            stats={r.get('name',r.get('Name')):r for r in rows}
        except (subprocess.SubprocessError,ValueError) as error: errors.append('Resource sampling: '+str(error)[:120])
    nodes=[]
    prefix='hypertrix-t4-'+run+'-'
    for c in containers:
        labels=c['Config'].get('Labels') or {}
        if labels.get('org.hypertrix.lab-role'):
            folder=Path(labels.get('org.hypertrix.work-root','/nonexistent')).resolve()
            if not folder.is_relative_to(Path('/var/lib/dawl')):continue
            nodes.append({'name':c['Name'],'role':labels['org.hypertrix.lab-role'],'id':c['Id'][:12],
                          'running':c['State']['Running'],'status':c['State']['Status'],'exit_code':c['State']['ExitCode'],
                          'stats':stats.get(c['Name'],{}),'dht':read(folder/'dht-status.json'),'events':[]})
            continue
        if labels.get('org.hypertrix.seed'):
            folder=Path(labels.get('org.hypertrix.work-root','/nonexistent')).resolve()
            if not folder.is_relative_to(Path('/var/lib/dawl')):continue
            activity=read(folder/'progress.json',{})
            identity=activity.get('execution_id')
            if identity and not (identity==labels.get('org.hypertrix.execution-id') or c['Id'].startswith(identity)):activity={}
            if not activity and not c['State']['Running']:continue
            if not activity and c['State']['Running'] and 'dawl_lab.verify' in (c['Config'].get('Cmd') or []):
                activity={'phase':'Verifying token windows','job':'Corpus verifier','status':'running',
                          'observed_at':datetime.now(timezone.utc).isoformat(),'execution_id':c['Id'][:12]}
            if folder.name=='probe' and activity:
                generation='Seed III' if folder.parent.name.startswith('seed-lab-') else 'Seed II'
                activity={**activity,'job':generation+' calibration'}
            if folder.name=='probe-replay' and activity:activity={**activity,'job':'Calibration replay'}
            age=None
            try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(activity['observed_at'])).total_seconds()
            except (ValueError,KeyError):pass
            # The full-model runner emits progress every ten updates (~35 s).
            # Keep its bounded reporting cadence explicit instead of making
            # healthy work appear paused between ordinary progress records.
            freshness=90 if folder.parent.name.startswith('seed-lab-') else 30
            live=c['State']['Running'] and age is not None and -5<=age<=freshness
            phase=activity.get('phase','Starting' if c['State']['Running'] else 'Stopped')
            if activity.get('status')=='running' and not live:phase='Updates paused' if c['State']['Running'] else 'Stopped'
            activity={**activity,'phase':phase,'live':live,'age_seconds':age,'freshness_seconds':freshness,'node_id':c['Id'][:12]}
            if not live:activity.pop('targets_per_second',None)
            compute=None
            if live and activity.get('target_tokens') and activity.get('parameters'):
                compute={'schema':'hypertrix.node-compute/1','observed_at':activity['observed_at'],
                    'counter_epoch':c['Id'],'kind':'estimated','convention':'scalar-fp-ops-fma2',
                    'flops_total':6*activity['parameters']*activity['target_tokens'],
                    'method':'6 * dense parameter count * training target tokens; excludes attention overhead, padding, evaluation and replay.'}
            nodes.append({'name':c['Name'],'role':labels.get('org.hypertrix.role','model-trainer'),
                'id':c['Id'][:12],'running':c['State']['Running'],'status':c['State']['Status'],
                'exit_code':c['State']['ExitCode'],'stats':stats.get(c['Name'],{}),'activity':activity,
                'compute':compute,'events':tail(folder/'events.jsonl',30)})
            continue
        name=c['Name'].removeprefix(prefix); folder=root/name; role_name=name
        recovery=labels.get('org.hypertrix.recovery')
        if recovery:
            if recovery!=current_recovery and not c['State']['Running']:continue
            mounts=[m for m in c.get('Mounts',[]) if m.get('Destination')=='/node']
            if len(mounts)!=1:continue # Helper processes are not network nodes.
            folder=Path(mounts[0]['Source']).resolve()
            if not folder.is_relative_to(Path('/var/lib/dawl')/('training-recovery-'+recovery)/'state'):continue
            role_name=folder.name
            name='recovery-'+recovery+'-'+c['Name'].removeprefix('hypertrix-r6-'+recovery+'-')
        role=('oracle' if role_name.startswith('validator-') else 'trainer' if role_name.startswith('trainer-')
              else 'gpu-verifier' if role_name.startswith('verifier-') else 'token-auditor' if role_name.startswith('worker-')
              else 'cortex' if role_name=='provider' else 'discovery' if role_name=='discovery' else 'console' if role_name=='console' else 'operator')
        if recovery and c['Name'].endswith('-discovery'):role='discovery'
        config=read(folder/'cluster.json',{}); public=None
        try:public=(folder/'key.public.hex').read_text().strip()
        except OSError:pass
        events=tail(folder/'events.jsonl')
        node={'name':name,'role':role,'container':c['Name'],'id':c['Id'][:12], 'image':c['Image'],
              'status':c['State']['Status'],'running':c['State']['Running'],'exit_code':c['State']['ExitCode'],
              'started_at':c['State']['StartedAt'],'finished_at':c['State']['FinishedAt'],
              'historical_attempt':name.endswith('-launch-failed'), 'public':public,
              'network':config.get('network_sha256'), 'experiment':'recovery-'+recovery if recovery else 'oracle-training-'+run,
              'host':config.get('training_host',config.get('abci_host','127.70.0.27' if role in ('cortex','discovery') else None)),
              'stats':stats.get(c['Name'],{}),'events':events,'dht':read(folder/'dht-status.json'),
              'final':read(folder/'public/final.json'),'replays':[],
              'compute':read(folder/'public/compute-metrics.json')}
        if role=='discovery':node['dht']=read((folder if recovery else root/'provider')/'dht-status.json')
        if role=='gpu-verifier':
            node['replays']=[read(p) for p in sorted((folder/'public').glob('replay-*.json'))]
        journal=tail(folder/'transfers.jsonl',10000)
        node['recent_transfers']=len(journal) # bounded tail, not a total counter
        nodes.append(node)
    gpu=None
    try:
        raw=command('/usr/lib/wsl/lib/nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw','--format=csv,noheader,nounits')
        values=[p.strip() for p in raw.strip().splitlines()[0].split(',')]
        def number(value):
            try:return float(value)
            except ValueError:return None
        gpu={'name':values[0],**dict(zip(('utilization_percent','memory_used_mib','memory_total_mib','temperature_c','power_w'),map(number,values[1:]))),
             'scope':'Shared physical GPU; utilization and memory are device-wide, not per-node attribution.'}
    except (OSError,subprocess.SubprocessError,IndexError) as error:errors.append('GPU telemetry: '+str(error)[:120])
    return {'observed_at':datetime.now(timezone.utc).isoformat(),'run':run,'nodes':nodes,'gpu':gpu,'errors':errors,
            'network_namespaces':namespace_counters(containers),
            'scope':'Operational telemetry only; node liveness and resource measurements are not quorum-certified claims.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--exchange',type=Path,required=True)
    p.add_argument('--run',default='003');p.add_argument('--once',action='store_true');a=p.parse_args();a.exchange.mkdir(parents=True,exist_ok=True)
    previous=None
    while True:
        try:
            value=collect(a.root,a.run);value['aggregate']=aggregate(value,previous);previous=value
            pending=a.exchange/'status.pending';pending.write_text(json.dumps(value));os.replace(pending,a.exchange/'status.json')
            activities=[n['activity'] for n in value['nodes'] if n.get('activity',{}).get('live') and n['activity'].get('status')=='running']
            if activities:
                with (a.exchange/'work-samples.jsonl').open('a') as stream:
                    stream.write(json.dumps({'observed_at':value['observed_at'],'gpu':value['gpu'],
                                             'aggregate':value['aggregate'],'activities':activities})+'\n')
        except Exception as error:print(type(error).__name__+': '+str(error)[:200],flush=True)
        if a.once:return
        time.sleep(4)


if __name__=='__main__':main()
