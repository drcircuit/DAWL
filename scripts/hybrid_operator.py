"""WSL operator for a fresh two-site pilot; private node keys stay at their site.

The trusted SSH host key must come from Podman logs or authenticated Azure logs.
This script never discovers or accepts a host key from the network.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import time
from urllib.request import build_opener, ProxyHandler

from hybrid_topology import members, endpoints

IMAGE = 'localhost/dawl-cluster:hybrid-v1'
SOURCE = Path('/var/lib/dawl/cluster-v2/provider-0')


def run(*args, data=None, timeout=900):
    result = subprocess.run(list(map(str, args)), input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-4000:])
    return result.stdout


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n')


def wait_for(check, seconds=180):
    deadline = time.monotonic()+seconds
    last = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value: return value
        except (RuntimeError, OSError, ValueError) as error: last = str(error)
        time.sleep(2)
    raise TimeoutError('Readiness deadline: '+str(last))


class Operator:
    def __init__(self, root):
        self.root = root.resolve()
        self.config = json.loads((root/'config.json').read_text())
        self.local = self.config['local_container']
        self.ssh_base = ['ssh', '-p', str(self.config['port']), '-i', str(root/'operator'),
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile='+str(root/'known_hosts'), '-o', 'ConnectTimeout=15']
        self.target = 'dawl@'+self.config['host']

    def remote(self, *args, data=None, timeout=900):
        return run(*self.ssh_base, self.target, shlex.join(list(map(str,args))), data=data, timeout=timeout)

    def local_cli(self, verb):
        return json.loads(run('podman','exec',self.local,'python','/app/dawl_cluster.py',verb,
                              '--root','/data/nodes/publisher'))

    def tunnel(self):
        # Bulk artifact streams must not share one SSH/TCP queue with consensus.
        opened=[]
        try:
            for socket,bulk in (('tunnel.sock',False),('data.sock',True)):
                args = [*self.ssh_base,'-M','-S',str(self.root/socket),'-f','-N',
                        '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3']
                for site,flag in (('cloud','-L'),('local','-R')):
                    for host,port in endpoints(site,self.config.get('nodes'),self.config.get('discovery',False)):
                        target='127.0.0.1:8882' if port==8902 else f'{host}:{port}'
                        if (port==8900)==bulk: args += [flag, f'{host}:{port}:{target}']
                run(*args,self.target);opened.append(socket)
        except Exception:
            for socket in opened:
                run(*self.ssh_base,'-S',str(self.root/socket),'-O','exit',self.target)
            raise

    def close_tunnel(self):
        for socket in ('data.sock','tunnel.sock'):
            if (self.root/socket).exists():
                run(*self.ssh_base,'-S',str(self.root/socket),'-O','exit',self.target)

    def gate(self, name):
        (self.root/'local'/name).touch()
        self.remote('touch','/data/'+name)

    def launch(self, bootstrap_only=False):
        if (self.root/'launch-started.json').exists():
            raise ValueError('Launch already attempted; preserve identities and inspect retained state')
        if datetime.now(timezone.utc) >= datetime.fromisoformat(self.config['deadline']):
            raise ValueError('Deadline passed')
        save(self.root/'launch-started.json',dict(started_at=datetime.now(timezone.utc).isoformat()))
        host = self.config['host'];port = self.config['port']
        (self.root/'known_hosts').write_text(f'[{host}]:{port} '+self.config['host_key']+'\n')
        self.remote('true')
        local = self.root/'local';local.mkdir()
        run('podman','run','-d','--name',self.local,'--network','host','--pids-limit','2048',
            '-v',str(local)+':/data','-v',str(SOURCE)+':/template:ro',
            '-e','DAWL_SITE=local','-e','DAWL_DEADLINE='+self.config['deadline'],IMAGE)
        self.tunnel()
        wait_for(lambda: len(list((local/'registrations').glob('*.json')))==20)
        public = self.root/'ceremony';regs=public/'registrations';regs.mkdir(parents=True)
        for path in (local/'registrations').glob('*.json'): shutil.copy2(path,regs/path.name)
        def cloud_regs():
            value=json.loads(self.remote('python','-c',
                "import json,pathlib; print(json.dumps({p.name:json.loads(p.read_text()) for p in pathlib.Path('/data/registrations').glob('*.json')}))"))
            return value if len(value)==7 else None
        for name,value in wait_for(cloud_regs).items(): save(regs/name,value)
        assembled=json.loads(run('podman','run','--rm','--network','none','-v',str(public)+':/ceremony',
            '-v',str(SOURCE)+':/template:ro','--entrypoint','python',IMAGE,'/app/dawl_external.py','assemble',
            '--root','/ceremony/bundle','--template','/template','--chain-id',self.config['chain_id'],
            '--image',self.config['image_pin'],'--registrations',
            *['/ceremony/registrations/'+m['name']+'.json' for m in members()]))
        save(self.root/'network-pins.json',assembled)
        shutil.copytree(public/'bundle',local/'bundle')
        for path in (local/'bundle').iterdir(): os.chown(path,1000,1000)
        archive=self.root/'public-bundle.tar'
        with tarfile.open(archive,'w') as tar: tar.add(public/'bundle',arcname='bundle')
        self.remote('tar','-xf','-','-C','/data',data=archive.read_bytes())
        pin=assembled['network_sha256']+'\n'
        (local/'network.sha256').write_text(pin)
        self.remote('python','-c',"from pathlib import Path; import sys; Path('/data/network.sha256').write_bytes(sys.stdin.buffer.read())",data=pin.encode())
        wait_for(lambda:(local/'installed').exists(),300)
        wait_for(lambda:self.remote('test','-f','/data/installed')==b'',300)
        self.gate('start')
        status=wait_for(lambda:self.local_cli('status'),300)
        save(self.root/'before-workers.json',status)
        print('Quorum-certified hybrid chain active: '+json.dumps({k:status[k] for k in
            ('chain_id','height','jobs','corpus_chunks','corpus_tokens')}),flush=True)
        if bootstrap_only: return
        index=json.loads((SOURCE/'artifact-index.json').read_text())
        archive=self.root/'public-objects.tar'
        with tarfile.open(archive,'w') as tar:
            for digest,descriptor in sorted(index.items()):
                path=SOURCE/'objects'/digest
                if digest!=descriptor['sha256'] or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                    raise ValueError('Source object digest mismatch')
                tar.add(path,arcname=digest,recursive=False)
        self.remote('mkdir','-p','/data/seed')
        started=time.monotonic()
        with archive.open('rb') as stream:
            result=subprocess.run([*self.ssh_base,self.target,'tar -xf - -C /data/seed'],stdin=stream,capture_output=True,timeout=1800)
        if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace'))
        save(self.root/'seed-transfer.json',dict(bytes=archive.stat().st_size,seconds=time.monotonic()-started))
        self.remote('python','/app/dawl_external.py','seed-provider','--root','/data/nodes/provider-0','--objects','/data/seed')
        run('podman','exec','--user','1000:1000',self.local,'python','/app/dawl_external.py','seed-provider',
            '--root','/data/nodes/provider-1','--objects','/template/objects')
        self.gate('provider-ready')
        opener=build_opener(ProxyHandler({}))
        for host,port in endpoints('cloud')+endpoints('local'):
            if port==8900: wait_for(lambda h=host:opener.open('http://'+h+':8900/health',timeout=5).status==200)
        self.gate('start-workers')
        save(self.root/'launched.json',dict(started_at=datetime.now(timezone.utc).isoformat(),workers=20,cloud_vcpus=3))
        print('Twenty auditors started across the two sites.',flush=True)

    def partition(self):
        opener=build_opener(ProxyHandler({}))
        def height(host):
            with opener.open('http://'+host+':26657/status',timeout=5) as response:
                return int(json.load(response)['result']['sync_info']['latest_block_height'])
        before=height('127.70.0.12')
        self.close_tunnel()
        try:
            time.sleep(12)
            local1=height('127.70.0.12')
            cloud1=json.loads(self.remote('python','-c',
                "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.70.0.10:26657/status'))['result']['sync_info']['latest_block_height'])"))
            time.sleep(15)
            local2=height('127.70.0.12')
            cloud2=json.loads(self.remote('python','-c',
                "import json,urllib.request; print(json.load(urllib.request.urlopen('http://127.70.0.10:26657/status'))['result']['sync_info']['latest_block_height'])"))
            if local1!=local2 or cloud1!=cloud2: raise ValueError('Partition did not halt both sides')
        finally: self.tunnel()
        after=wait_for(lambda:(h if (h:=height('127.70.0.12'))>local2 else None),240)
        save(self.root/'partition.json',dict(before=before,local_halted_at=local2,cloud_halted_at=int(cloud2),
                                            resumed_at=after,stable_observation_seconds=15))
        print('Both sites halted without quorum and resumed after reconnection.',flush=True)

    def complete(self):
        results=self.root/'results';results.mkdir(exist_ok=True)
        expiry=datetime.fromisoformat(self.config['deadline'])
        while datetime.now(timezone.utc)<expiry:
            status=self.local_cli('status')
            with (results/'progress.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),**status))+'\n')
            print(f"Height {status['height']}: {status['complete']}/{status['jobs']} jobs; {status['attestations']} attestations",flush=True)
            if status['disputed']: raise ValueError('Disputed audit; do not publish')
            if status['complete']==status['jobs']: break
            time.sleep(20)
        else: raise TimeoutError('No full-corpus completion before deadline')
        release=self.local_cli('publish');save(results/'release.json',release)
        certificate=self.certificate()
        if not certificate['verified'] or certificate['state']['dataset_release']['id']!=release['dataset_release']:
            raise ValueError('Release certificate mismatch')
        save(results/'public-certified-state.json',certificate)
        save(results/'status.json',self.local_cli('status'))
        counts=self.counters()
        save(results/'worker-counters.json',counts)
        elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(json.loads((self.root/'launched.json').read_text())['started_at'])).total_seconds()
        save(results/'report.json',dict(completed=True,workers=20,cloud_vcpus=3,failure_domains=2,seconds=elapsed,
            corpus_chunks=status['corpus_chunks'],corpus_tokens=status['corpus_tokens'],attestations=status['attestations'],
            verified_chunk_executions=status['verified_chunk_executions'],dataset_release=release['dataset_release'],
            training_executed=False,limits=['Two shared hosts controlled by one operator',
                'Elapsed time includes transfer and orchestration; no comparative throughput claim']))
        print('Certified full-corpus release and audit counters captured.',flush=True)

    def certificate(self):
        pins=json.loads((self.root/'network-pins.json').read_text())
        return json.loads(run('podman','exec',self.local,'dawl-consensus','verify',
            '--genesis','/data/nodes/publisher/genesis.json','--genesis-sha256',pins['genesis_sha256'],
            '--rpc','http://127.70.0.10:26657','--max-age','120s'))

    def counters(self):
        code="import pathlib,sqlite3,json; print(json.dumps({p.parent.name:dict(sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True).execute('SELECT name,value FROM counters')) for p in pathlib.Path('/data/nodes').glob('worker-*/worker.sqlite')}))"
        counts=json.loads(run('podman','exec',self.local,'python','-c',code))
        counts.update(json.loads(self.remote('python','-c',code)))
        return counts

    def export_public(self, destination):
        destination.mkdir(parents=True,exist_ok=True)
        # Explicit allowlist: never copy node directories, operator keys or private archives.
        for name in ('network-pins.json','before-workers.json','launched.json','seed-transfer.json','partition.json',
                     'runtime-limits.json','http-latency.json','transport-adjustment.json'):
            source=self.root/name
            if source.exists(): shutil.copy2(source,destination/name)
        for name in ('ceremony','results'):
            source=self.root/name
            if source.exists(): shutil.copytree(source,destination/name,dirs_exist_ok=True)
        print('Public pilot evidence exported to '+str(destination),flush=True)


def rehearsal(root):
    if root.exists(): raise ValueError('Fresh rehearsal directory required')
    root.mkdir(parents=True,mode=0o700)
    run('ssh-keygen','-q','-t','ed25519','-N','','-f',root/'operator')
    deadline=(datetime.now(timezone.utc)+timedelta(minutes=35)).isoformat()
    for name in ('cloud','persist'): (root/name).mkdir()
    container='dawl-hybrid-rehearsal-cloud'
    run('podman','run','-d','--name',container,'--cpus','3','--memory','8g','--pids-limit','2048',
        '-p','127.0.0.1:8999:2222','-v',str(root/'cloud')+':/data','-v',str(root/'persist')+':/persist',
        '-e','DAWL_SITE=cloud','-e','DAWL_DEADLINE='+deadline,
        '-e','DAWL_OPERATOR_PUBLIC_KEY='+(root/'operator.pub').read_text().strip(),IMAGE)
    def hostkey():
        lines=run('podman','logs',container).decode().splitlines()
        return next((l.split(' ',1)[1] for l in lines if l.startswith('DAWL_SSH_HOST_KEY ')),None)
    # Podman image manifest is immutable locally; this rehearsal pin is not a registry claim.
    manifest=run('podman','image','inspect',IMAGE,'--format','{{.Digest}}').decode().strip()
    save(root/'config.json',dict(host='127.0.0.1',port=8999,host_key=wait_for(hostkey),deadline=deadline,
        local_container='dawl-hybrid-rehearsal-local',chain_id='dawl-hybrid-rehearsal-001',
        image_pin='localhost/dawl-cluster@'+manifest,cloud_container=container))
    operator=Operator(root)
    operator.launch(bootstrap_only=True)
    operator.partition()
    operator.close_tunnel()
    run('podman','stop','--time','90',operator.local,container)
    print('Local rehearsal complete; stopped containers and preserved state.',flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['rehearse','launch','status','complete','partition','export-public','stop-local'])
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--bootstrap-only',action='store_true')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.command=='rehearse': return rehearsal(a.root)
    op=Operator(a.root)
    if a.command=='launch': op.launch(a.bootstrap_only)
    elif a.command=='status': print(json.dumps(op.local_cli('status'),indent=2))
    elif a.command=='complete': op.complete()
    elif a.command=='partition': op.partition()
    elif a.command=='export-public':
        if not a.output: raise ValueError('--output required')
        op.export_public(a.output)
    elif a.command=='stop-local':
        op.close_tunnel();run('podman','stop','--time','90',op.local)


if __name__=='__main__': main()
