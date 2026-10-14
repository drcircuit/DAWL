"""Pilot supervisor. Live SQLite stays on local disk, never on the Azure file share.

Cloud restart is fenced by a persistent marker; snapshots are forensic exports,
not permission to restore a validator with potentially stale signing state.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, '/app/src')
from dawl_cluster.external import identity, install, validate_registration
from dawl_platform.workflow import implementation_digest
from hybrid_topology import members, endpoints


def main():
    site = os.environ['DAWL_SITE']
    if site not in ('cloud', 'local'): raise ValueError('invalid site')
    deadline = datetime.fromisoformat(os.environ['DAWL_DEADLINE'].replace('Z', '+00:00'))
    remaining = (deadline-datetime.now(timezone.utc)).total_seconds()
    if not 0 < remaining <= 4*3600: raise ValueError('fresh deadline within four hours required')
    root = Path('/data'); root.mkdir(exist_ok=True); os.chown(root, 1000, 1000)
    children = {}; stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    def expired(): return stopping or datetime.now(timezone.utc) >= deadline
    def demote():
        os.setgroups([]); os.setgid(1000); os.setuid(1000)
    def save(path, value):
        path.write_text(json.dumps(value, indent=2)+'\n')
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    cloud_marker = Path('/persist/started.json')
    if site == 'cloud':
        with cloud_marker.open('x') as stream:
            json.dump(dict(started_at=datetime.now(timezone.utc).isoformat(), deadline=deadline.isoformat(),
                           restart_policy='Never restore a signed validator from this pilot automatically'), stream)
    try:
        if site == 'cloud':
            ssh = Path('/run/dawl-ssh'); ssh.mkdir(parents=True)
            Path('/run/sshd').mkdir(exist_ok=True)
            subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(ssh/'host')],check=True)
            key = os.environ['DAWL_OPERATOR_PUBLIC_KEY'].strip()
            if not key.startswith('ssh-ed25519 ') or '\n' in key: raise ValueError('invalid operator public key')
            auth=Path('/home/dawl/.ssh');auth.mkdir(mode=0o700,exist_ok=True);os.chown(auth,1000,1000)
            (auth/'authorized_keys').write_text(key+'\n');os.chmod(auth/'authorized_keys',0o600);os.chown(auth/'authorized_keys',1000,1000)
            config = '\n'.join([
                'Port 2222', 'ListenAddress 0.0.0.0', 'HostKey '+str(ssh/'host'),
                'PidFile '+str(ssh/'pid'), 'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
                'PermitEmptyPasswords no', 'PermitRootLogin no', 'UsePAM no', 'AllowUsers dawl',
                'AllowAgentForwarding no', 'X11Forwarding no', 'GatewayPorts clientspecified',
                'AllowTcpForwarding yes', 'PermitOpen '+' '.join(f'{h}:{p}' for h,p in endpoints('cloud')),
                'PermitListen '+' '.join(f'{h}:{p}' for h,p in endpoints('local')),
                'Subsystem sftp internal-sftp', 'LogLevel ERROR'])+'\n'
            (ssh/'config').write_text(config)
            subprocess.run(['/usr/sbin/sshd','-t','-f',str(ssh/'config')],check=True)
            children['ssh'] = subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(ssh/'config')])
            print('DAWL_SSH_HOST_KEY '+(ssh/'host.pub').read_text().strip(),flush=True)
        nodes = [m for m in members() if m['site'] == site]
        exports = root/'registrations';exports.mkdir(exist_ok=True);os.chown(exports,1000,1000)
        for member in nodes:
            node = root/'nodes'/member['name']
            if not node.exists(): identity(node,member['name'],member['role'],member['host'],transport='ssh-loopback/1')
            public = json.loads((node/'registration.json').read_text())
            registered = validate_registration(public)
            if any(registered[k] != member[k] for k in ('name','role','host')): raise ValueError('stored identity mismatch')
            save(exports/(member['name']+'.json'),public)
        for parent, dirs, files in os.walk(root):
            os.chown(parent,1000,1000)
            for name in files: os.chown(Path(parent)/name,1000,1000)
        print('DAWL_REGISTRATIONS_READY '+site,flush=True)
        while not (root/'network.sha256').exists():
            if expired(): return
            time.sleep(1)
        pin=(root/'network.sha256').read_text().strip()
        for member in nodes:
            node=root/'nodes'/member['name']
            if not (node/'installed.json').exists():
                subprocess.run([sys.executable,'/app/dawl_external.py','install','--root',str(node),
                                '--bundle',str(root/'bundle'),'--network-sha256',pin],preexec_fn=demote,check=True)
            if json.loads((node/'installed.json').read_text())['network_sha256'] != pin: raise ValueError('network changed')
            if json.loads((node/'plan.json').read_text())['implementation_sha256'] != implementation_digest(): raise ValueError('source changed')
        (root/'installed').touch()
        launched=set()
        while not expired():
            for name,child in children.items():
                if child.poll() is not None: raise RuntimeError(f'{name} exited unexpectedly: {child.returncode}')
            for member in nodes:
                name,role,host=(member[k] for k in ('name','role','host'))
                if name in launched or role == 'publisher': continue
                gate = 'start-workers' if role == 'worker' else 'provider-ready' if role == 'provider' else 'start'
                if not (root/gate).exists(): continue
                node=root/'nodes'/name
                args=['validator',str(node)] if role == 'validator' else (
                     ['worker','--root',str(node)] if role == 'worker' else
                     ['provider','--root',str(node),'--host',host,'--port','8900','--allow-insecure-lab'])
                log=(root/(name+'.log')).open('ab',buffering=0)
                children[name]=subprocess.Popen([sys.executable,'/app/scripts/cluster_entrypoint.py',*args],
                                                stdout=log,stderr=subprocess.STDOUT,preexec_fn=demote)
                log.close();launched.add(name)
                save(root/'running.json',dict(site=site,children={n:c.pid for n,c in children.items()}))
            time.sleep(.5)
    finally:
        for name,child in reversed(list(children.items())):
            if child.poll() is None: child.terminate()
        for child in children.values():
            try: child.wait(timeout=45)
            except subprocess.TimeoutExpired: child.kill();child.wait()
        save(root/'stopped.json',dict(stopped_at=datetime.now(timezone.utc).isoformat(),
                                    exits={n:c.returncode for n,c in children.items()}))
        if site == 'cloud':
            with tarfile.open('/persist/private-state.tar','w') as archive:
                for path in root.rglob('*'):
                    relative=path.relative_to(root)
                    if path.is_file() and not {'objects','seed'}.intersection(relative.parts):
                        archive.add(path,arcname=str(relative),recursive=False)
            Path('/persist/stopped.json').write_bytes((root/'stopped.json').read_bytes())
        print('DAWL_POOL_STOPPED '+site,flush=True)


if __name__ == '__main__': main()
