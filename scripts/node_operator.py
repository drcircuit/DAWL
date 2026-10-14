"""One-container-per-node hybrid bootstrap, monitoring and pilot evidence."""
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from urllib.request import build_opener,ProxyHandler

from hybrid_operator import Operator,run,save,wait_for,SOURCE
from hybrid_topology import endpoints
from node_topology import default_topology,validate


def image(role): return 'localhost/dawl-'+role+':node-v1'


class NodeOperator(Operator):
    def container(self,name): return self.config['prefix']+'-'+name

    @property
    def publisher(self): return next(n['name'] for n in self.config['nodes'] if n['role']=='publisher')

    def local_cli(self,verb):
        return json.loads(run('podman','exec',self.container(self.publisher),'python','/app/dawl_cluster.py',verb,'--root','/node/state'))

    def certificate(self):
        pin=json.loads((self.root/'network-pins.json').read_text())['genesis_sha256']
        return json.loads(run('podman','exec',self.container(self.publisher),'dawl-consensus','verify',
            '--genesis','/node/state/genesis.json','--genesis-sha256',pin,'--rpc','http://127.70.0.10:26657','--max-age','120s'))

    def telemetry(self):
        records={}
        for path in (self.root/'exchange/status').glob('*.json'): records[path.stem]=json.loads(path.read_text())
        for path in (self.root/'exchange/contributors').glob('*/*.json'): records[path.stem]=json.loads(path.read_text())
        http=build_opener(ProxyHandler({}))
        with http.open('http://127.70.0.31:8901/status',timeout=10) as response:
            records.update({n['name']:n for n in json.load(response)['nodes']})
        return records

    def counters(self):
        records=self.telemetry()
        save(self.root/'results/node-telemetry.json',records)
        return {n:v.get('counters',{}) for n,v in records.items() if v['role']=='worker'}

    def gate(self,name):
        (self.root/'exchange'/name).touch();self.remote('touch','/exchange/'+name)

    def start_node(self,node,*,cloud=False,pod=None):
        native=self.root/('cloud-nodes' if cloud else 'nodes')/node['name']
        private=native/'private';persist=native/'archive'
        private.mkdir(parents=True);persist.mkdir()
        exchange=self.root/('cloud-exchange' if cloud else 'exchange')
        args=['podman','run','-d','--name',self.container(node['name']),
            '--pids-limit','256','--cpus',str(node['cpu']),'--memory',str(node['memory_gb'])+'g',
            '-v',str(private)+':/node','-v',str(persist)+':/persist','-v',str(exchange)+':/exchange']
        args+=['--pod',pod] if pod else ['--network','host']
        env=dict(DAWL_NAME=node['name'],DAWL_HOST=node['host'],DAWL_SITE=node['site'],DAWL_DEADLINE=self.config['deadline'],
                 DAWL_IMAGE_PIN=self.config['role_images'][node['role']])
        if not cloud and node['role']=='provider':
            args+=['-v',str(SOURCE/'objects')+':/seed:ro'];env['DAWL_SEED_OBJECTS']='/seed'
        for key,value in env.items(): args+=['-e',key+'='+value]
        run(*args,image(node['role']))

    def monitor(self):
        observer=self.root/'observer';observer.mkdir(exist_ok=True)
        for name in ('cluster.json','plan.json','genesis.json'):
            shutil.copy2(self.root/'nodes'/self.publisher/'private/state'/name,observer/name)
            os.chown(observer/name,1000,1000)
        save(observer/'topology.json',self.config['topology']);os.chown(observer/'topology.json',1000,1000)
        os.chown(observer,1000,1000)
        run('podman','run','-d','--name',self.container('observer'),'--network','host','--cpus','1','--memory','1g',
            '--cap-drop','ALL','--security-opt','no-new-privileges',
            '-v',str(observer)+':/observer','-v',str(self.root/'exchange')+':/exchange:ro',image('observer'))
        print('The Hypertrix Console: http://127.0.0.1:8882/',flush=True)

    def launch(self,bootstrap_only=False):
        if (self.root/'launch-started.json').exists(): raise ValueError('Launch already attempted; preserve state and inspect')
        validate(self.config['topology'])
        save(self.root/'launch-started.json',dict(started_at=datetime.now(timezone.utc).isoformat()))
        (self.root/'known_hosts').write_text(f"[{self.config['host']}]:{self.config['port']} "+self.config['host_key']+'\n')
        self.remote('true')
        exchange=self.root/'exchange';exchange.mkdir();os.chown(exchange,1000,1000)
        nodes=self.config['nodes'];local=[n for n in nodes if n['site']=='local']
        for node in local: self.start_node(node)
        self.tunnel()
        wait_for(lambda:len(list((exchange/'registrations').glob('*.json')))==len(local),240)
        public=self.root/'ceremony';registrations=public/'registrations';registrations.mkdir(parents=True)
        for path in (exchange/'registrations').glob('*.json'): shutil.copy2(path,registrations/path.name)
        def cloud_regs():
            value=json.loads(self.remote('python','-c',
                "import pathlib,json; print(json.dumps({p.name:json.loads(p.read_text()) for p in pathlib.Path('/exchange/registrations').glob('*.json')}))"))
            return value if len(value)==len(nodes)-len(local) else None
        for name,value in wait_for(cloud_regs,240).items(): save(registrations/name,value)
        save(public/'role-images.json',self.config['role_images'])
        pins=json.loads(run('podman','run','--rm','--network','none','-v',str(public)+':/ceremony',
            '-v',str(SOURCE)+':/template:ro','--entrypoint','python',image('validator'),'/app/dawl_external.py','assemble',
            '--root','/ceremony/bundle','--template','/template','--chain-id',self.config['chain_id'],
            '--image',self.config['role_images']['validator'],'--role-images','/ceremony/role-images.json',
            '--registrations',*['/ceremony/registrations/'+n['name']+'.json' for n in nodes]))
        save(self.root/'network-pins.json',pins)
        shutil.copytree(public/'bundle',exchange/'bundle')
        for path in (exchange/'bundle').iterdir(): os.chown(path,1000,1000);os.chmod(path,0o644)
        archive=self.root/'public-bundle.tar'
        with tarfile.open(archive,'w') as tar: tar.add(public/'bundle',arcname='bundle')
        self.remote('tar','-xf','-','-C','/exchange',data=archive.read_bytes())
        self.remote('chmod','-R','a+rX','/exchange/bundle')
        pin=pins['network_sha256']+'\n';(exchange/'network.sha256').write_text(pin)
        self.remote('python','-c',"from pathlib import Path; import sys; Path('/exchange/network.sha256').write_bytes(sys.stdin.buffer.read())",data=pin.encode())
        def installed():
            values=self.telemetry()
            return len(values)==len(nodes) and all(n['phase']=='installed' for n in values.values())
        wait_for(installed,300)
        self.gate('start')
        initial=wait_for(lambda:self.local_cli('status'),300);save(self.root/'before-workers.json',initial)
        print('Four individually containerized validators formed the fresh certified chain.',flush=True)
        self.monitor()
        if bootstrap_only: return
        index=json.loads((SOURCE/'artifact-index.json').read_text());archive=self.root/'public-objects.tar'
        with tarfile.open(archive,'w') as tar:
            for digest,descriptor in sorted(index.items()):
                path=SOURCE/'objects'/digest
                if descriptor['sha256']!=digest or hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('Invalid source object')
                tar.add(path,arcname=digest,recursive=False)
        self.remote('mkdir','-p','/exchange/seed');started=time.monotonic()
        with archive.open('rb') as stream:
            result=subprocess.run([*self.ssh_base,self.target,'tar -xf - -C /exchange/seed'],stdin=stream,capture_output=True,timeout=1800)
        if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace'))
        self.remote('chmod','-R','a+rX','/exchange/seed')
        save(self.root/'seed-transfer.json',dict(bytes=archive.stat().st_size,seconds=time.monotonic()-started))
        self.gate('seed-ready')
        wait_for(lambda:all(n['phase']=='running' for n in self.telemetry().values() if n['role']=='provider'),300)
        self.gate('start-workers')
        save(self.root/'launched.json',dict(started_at=datetime.now(timezone.utc).isoformat(),workers=20,cloud_vcpus=3,
                                           node_containers=len(nodes),topology=self.config['topology']))
        print('Twenty separate auditor containers released; monitoring and evidence capture are ready.',flush=True)

    def stop_local(self):
        (self.root/'exchange/stop').touch()
        run('podman','stop','--time','60',*[self.container(n['name']) for n in self.config['nodes'] if n['site']=='local'],
            self.container('observer'))
        self.close_tunnel()

    def node_faults(self):
        """One cloud validator process outage and one same-state local auditor restart."""
        http=build_opener(ProxyHandler({}))
        oracle=next(n for n in self.config['nodes'] if n['role']=='validator' and n['site']=='cloud')
        local=next(n for n in self.config['nodes'] if n['role']=='validator' and n['site']=='local')
        def height():
            with http.open('http://'+local['host']+':26657/status',timeout=5) as response:
                return int(json.load(response)['result']['sync_info']['latest_block_height'])
        before=height()
        self.remote('mkdir','-p','/exchange/control')
        flag='/exchange/control/'+oracle['name']+'.pause'
        self.remote('touch',flag)
        try:
            wait_for(lambda:self.telemetry()[oracle['name']]['phase']=='paused',30)
            paused_at=height()
            progressed=wait_for(lambda:(h if (h:=height())>=paused_at+3 else None),120)
        finally: self.remote('rm','-f',flag)
        wait_for(lambda:self.telemetry()[oracle['name']]['phase']=='running',30)
        daemon=next(n for n in self.config['nodes'] if n['role']=='worker' and n['site']=='local')
        registration=self.root/'nodes'/daemon['name']/'private/state/registration.json'
        identity_before=registration.read_bytes()
        run('podman','stop','--time','60',self.container(daemon['name']))
        run('podman','start',self.container(daemon['name']))
        wait_for(lambda:self.telemetry()[daemon['name']]['phase']=='running',60)
        if registration.read_bytes()!=identity_before: raise ValueError('Restart changed node identity')
        save(self.root/'node-faults.json',dict(validator=oracle['name'],before=before,paused_at=paused_at,
            advanced_with_one_validator_paused=progressed,validator_resumed=True,
            restarted_daemon=daemon['name'],same_registration=True,private_state_retained=True))
        print('Consensus advanced with one Oracle paused; a separate Daemon restarted with its original identity.',flush=True)

    def export_public(self,destination):
        super().export_public(destination)
        for name in ('node-faults.json','private-interface-test.json','console-validation.json'):
            if (self.root/name).exists(): shutil.copy2(self.root/name,destination/name)
        save(destination/'topology.json',self.config['topology'])


def rehearse(root):
    if root.exists(): raise ValueError('Fresh rehearsal directory required')
    root.mkdir(parents=True,mode=0o700)
    run('ssh-keygen','-q','-t','ed25519','-N','','-f',root/'operator')
    topology=validate(default_topology());prefix='hypertrix-rehearsal';deadline=(datetime.now(timezone.utc)+timedelta(minutes=40)).isoformat()
    pins={role:'localhost/dawl-'+role+'@'+run('podman','image','inspect',image(role),'--format','{{.Digest}}').decode().strip()
          for role in ('validator','provider','worker','publisher')}
    config=dict(prefix=prefix,local_container=prefix+'-blueprint-publisher',host='127.0.0.1',port=8998,deadline=deadline,
                chain_id=prefix,role_images=pins,nodes=topology['nodes'],topology=topology)
    save(root/'config.json',config)
    cloud=root/'cloud-exchange';cloud.mkdir();os.chown(cloud,1000,1000)
    pod=prefix+'-cloud'
    run('podman','pod','create','--name',pod,'-p','127.0.0.1:8998:2222')
    run('podman','run','-d','--name',prefix+'-gateway','--pod',pod,'--cpus','.5','--memory','.5g',
        '-v',str(cloud)+':/exchange','-e','DAWL_DEADLINE='+deadline,'-e','DAWL_NODES='+json.dumps(topology['nodes']),
        '-e','DAWL_OPERATOR_PUBLIC_KEY='+(root/'operator.pub').read_text().strip(),image('gateway'))
    def hostkey():
        return next((line.split(' ',1)[1] for line in run('podman','logs',prefix+'-gateway').decode().splitlines() if line.startswith('DAWL_SSH_HOST_KEY ')),None)
    config['host_key']=wait_for(hostkey);save(root/'config.json',config)
    op=NodeOperator(root)
    for node in topology['nodes']:
        if node['site']=='cloud': op.start_node(node,cloud=True,pod=pod)
    op.launch(bootstrap_only=True)
    # The worker's filesystem has no validator volume and no private ABCI TCP listener.
    auditor=next(n['name'] for n in topology['nodes'] if n['role']=='worker' and n['site']=='local')
    isolation=json.loads(run('podman','exec',op.container(auditor),'python','-c',
        "import socket,json,pathlib; result={};\nfor port in (26658,26659):\n s=socket.socket();s.settimeout(1);result[str(port)]=s.connect_ex(('127.70.0.12',port));s.close()\nprint(json.dumps(result))"))
    if not all(isolation.values()): raise ValueError('Private ABCI reachable from worker container')
    save(root/'private-interface-test.json',isolation)
    op.partition()
    op.gate('stop');time.sleep(5)
    op.stop_local();run('podman','pod','stop','--time','60',pod)
    print('Per-node container rehearsal passed; state retained and containers stopped.',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['rehearse','launch','status','complete','partition','node-faults','export-public','stop-local'])
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path);p.add_argument('--bootstrap-only',action='store_true')
    a=p.parse_args()
    if a.command=='rehearse': return rehearse(a.root)
    op=NodeOperator(a.root)
    if a.command=='launch': op.launch(a.bootstrap_only)
    elif a.command=='status': print(json.dumps(op.local_cli('status'),indent=2))
    elif a.command=='complete': op.complete()
    elif a.command=='partition': op.partition()
    elif a.command=='node-faults': op.node_faults()
    elif a.command=='stop-local': op.stop_local()
    elif a.command=='export-public':
        if not a.output: raise ValueError('--output required')
        op.export_public(a.output)


if __name__=='__main__': main()
