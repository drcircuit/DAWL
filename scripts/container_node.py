"""One admitted DAWL node per container; no other node's private volume is mounted."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time

sys.path.insert(0,'/app/src')
from dawl_cluster.external import identity, install, validate_registration, check_bundle, seed_provider
from dawl_platform.workflow import implementation_digest
from runtime_upgrade import verify_upgrade,restore_closed


def atomic(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,sort_keys=True)+'\n')
    os.chmod(temporary,0o644)
    temporary.replace(path)


def resources():
    values={}
    for name,paths in {
        'cpu_stat':['/sys/fs/cgroup/cpu.stat','/sys/fs/cgroup/cpu/cpu.stat'],
        'cpu_usage_ns':['/sys/fs/cgroup/cpuacct/cpuacct.usage'],
        'cpu_max':['/sys/fs/cgroup/cpu.max'],
        'cpu_quota_us':['/sys/fs/cgroup/cpu/cpu.cfs_quota_us'],
        'cpu_period_us':['/sys/fs/cgroup/cpu/cpu.cfs_period_us'],
        'memory_bytes':['/sys/fs/cgroup/memory.current','/sys/fs/cgroup/memory/memory.usage_in_bytes'],
        'memory_limit_bytes':['/sys/fs/cgroup/memory.max','/sys/fs/cgroup/memory/memory.limit_in_bytes'],
    }.items():
        for candidate in paths:
            path=Path(candidate)
            if path.exists(): values[name]=path.read_text().strip();break
    return values


def main():
    name,role,host,site=(os.environ[k] for k in ('DAWL_NAME','DAWL_ROLE','DAWL_HOST','DAWL_SITE'))
    deadline=datetime.fromisoformat(os.environ['DAWL_DEADLINE'])
    if not 0<(deadline-datetime.now(timezone.utc)).total_seconds()<=86400: raise ValueError('expired or unbounded pilot')
    base=Path('/node');root=base/'state';exchange=Path('/exchange');persist=Path('/persist')
    for path in (base,exchange): path.mkdir(exist_ok=True);os.chown(path,1000,1000)
    child=None;peer=None;stopping=False;paused=False;started=time.monotonic();phase='initializing'
    def stop(*_):
        nonlocal stopping
        stopping=True
    def expired():
        return stopping or datetime.now(timezone.utc)>=deadline or (exchange/'stop').exists() or (exchange/'control'/(name+'.stop')).exists()
    def demote(): os.setgroups([]);os.setgid(1000);os.setuid(1000)
    def own_state():
        for parent,dirs,files in os.walk(base):
            os.chown(parent,1000,1000)
            for filename in files: os.chown(Path(parent)/filename,1000,1000)
    def snapshot():
        counters={};events=[]
        database=root/'worker.sqlite'
        if database.exists():
            try:
                with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True,timeout=1) as db:
                    counters=dict(db.execute('SELECT name,value FROM counters'))
                    events=[json.loads(row[0]) for row in db.execute('SELECT payload FROM events ORDER BY id DESC LIMIT 8')]
            except sqlite3.OperationalError:
                pass  # Telemetry must not terminate a node while its database is initializing.
        value=dict(name=name,role=role,host=host,site=site,container=socket.gethostname(),phase=phase,
            observed_at=datetime.now(timezone.utc).isoformat(),uptime_seconds=round(time.monotonic()-started,1),
            child_pid=child.pid if child else None,resources=resources(),counters=counters,events=events,
            image=os.environ.get('DAWL_IMAGE_PIN'),network_sha256=(exchange/'network.sha256').read_text().strip() if (exchange/'network.sha256').exists() else None)
        if (root/'dht-status.json').exists():value['dht']=json.loads((root/'dht-status.json').read_text())
        atomic(exchange/'status'/(name+'.json'),value)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    fence=persist/'started.json'
    if os.environ.get('DAWL_RESTORE_SHA256'):restore_closed(root,persist,os.environ['DAWL_RESTORE_SHA256'])
    if fence.exists() and not (root/'key.private.pem').exists():
        raise ValueError('Private volume missing after prior start; refuse signing-state reset')
    try:
        if not root.exists(): identity(root,name,role,host,transport='ssh-loopback/1')
        public=json.loads((root/'registration.json').read_text());registered=validate_registration(public)
        if any(registered[k]!=v for k,v in dict(name=name,role=role,host=host).items()): raise ValueError('Identity differs from container assignment')
        if fence.exists():
            if json.loads(fence.read_text())['signer']!=registered['signer']: raise ValueError('Archive fence signer mismatch')
        else:
            persist.mkdir(exist_ok=True)
            with fence.open('x') as stream: json.dump(dict(name=name,signer=registered['signer'],started_at=datetime.now(timezone.utc).isoformat()),stream)
        own_state()
        atomic(exchange/'registrations'/(name+'.json'),public)
        phase='awaiting-bootstrap';snapshot()
        while not (exchange/'network.sha256').exists():
            if expired(): return
            time.sleep(1)
        pin=(exchange/'network.sha256').read_text().strip();bundle=exchange/'bundle'
        manifest=check_bundle(bundle,pin)
        upgrade=exchange/'runtime-upgrade.json'
        while os.environ.get('DAWL_UPGRADE')=='1' and not upgrade.exists():
            if expired():return
            time.sleep(1)
        if upgrade.exists():
            verify_upgrade(upgrade,pin,json.loads((bundle/'plan.json').read_text()),role,os.environ['DAWL_IMAGE_PIN'],implementation_digest(),os.environ['DAWL_DEADLINE'])
        elif manifest.get('role_images',{}).get(role)!=os.environ['DAWL_IMAGE_PIN']: raise ValueError('Role image differs from agreed network pin')
        if not (root/'installed.json').exists(): install(root,bundle,pin)
        if json.loads((root/'installed.json').read_text())['network_sha256']!=pin: raise ValueError('Installed network changed')
        if not upgrade.exists() and json.loads((root/'plan.json').read_text())['implementation_sha256']!=implementation_digest(): raise ValueError('Source pin changed')
        own_state();phase='installed';snapshot()
        gate='start-workers' if role=='worker' else 'seed-ready' if role=='provider' else 'start'
        while not (exchange/gate).exists():
            if expired(): return
            snapshot();time.sleep(2)
        if role=='provider':
            phase='seeding';snapshot()
            seed_provider(root,Path(os.environ.get('DAWL_SEED_OBJECTS','/exchange/seed')))
            own_state()
        if os.environ.get('DAWL_DHT')=='1':
            hats={'validator':'VALIDATOR,LEDGER_REPLICA','provider':'PROVIDER','worker':'AUDITOR','publisher':'LEDGER_REPLICA'}
            peer=subprocess.Popen([sys.executable,'/app/scripts/peer_node.py','--root',str(root),'--name',name,'--host',host,
                '--roles',hats[role],'--seeds','["http://127.70.0.20:8903","http://127.70.0.21:8903"]'],preexec_fn=demote,start_new_session=True)
        if role!='publisher':
            args=['validator',str(root)] if role=='validator' else ['worker','--root',str(root)] if role=='worker' else [
                'provider','--root',str(root),'--host',host,'--port','8900','--allow-insecure-lab']
            child=subprocess.Popen([sys.executable,'/app/scripts/cluster_entrypoint.py',*args],preexec_fn=demote,start_new_session=True)
        phase='running';snapshot()
        while not expired():
            if peer and peer.poll() is not None:raise RuntimeError('DHT child exited')
            if child and child.poll() is not None: raise RuntimeError(f'{name} exited with {child.returncode}')
            requested=(exchange/'control'/(name+'.pause')).exists()
            if child and requested!=paused:
                os.killpg(child.pid,signal.SIGSTOP if requested else signal.SIGCONT)
                paused=requested;phase='paused' if paused else 'running'
            snapshot();time.sleep(3)
    finally:
        if peer and peer.poll() is None:
            peer.terminate()
            try:peer.wait(timeout=15)
            except subprocess.TimeoutExpired:peer.kill();peer.wait()
        if child and child.poll() is None:
            if paused: os.killpg(child.pid,signal.SIGCONT)
            child.terminate()
            try: child.wait(timeout=40)
            except subprocess.TimeoutExpired: os.killpg(child.pid,signal.SIGKILL);child.wait()
        phase='stopped'
        snapshot()
        # Closed state only. Caches and public bulk objects do not belong in the key archive.
        if root.exists():
            with tarfile.open(persist/'private-state.tmp','w') as archive:
                for path in root.rglob('*'):
                    relative=path.relative_to(root)
                    if path.is_file() and not {'objects','pieces','seed'}.intersection(relative.parts):
                        archive.add(path,arcname=str(relative),recursive=False)
            (persist/'private-state.tmp').replace(persist/'private-state.tar')
            atomic(persist/'stopped.json',dict(name=name,at=datetime.now(timezone.utc).isoformat(),exit=child.returncode if child else 0))
        print('DAWL_NODE_STOPPED '+name,flush=True)


if __name__=='__main__': main()
