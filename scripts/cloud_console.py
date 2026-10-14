"""Read-only HTTPS Console; no node signing keys or control endpoints."""
from datetime import datetime,timezone
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def main():
    deadline=datetime.fromisoformat(os.environ['DAWL_DEADLINE']);stopping=False;children=[]
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    source=Path('/exchange/observer');root=Path('/observer');root.mkdir(exist_ok=True)
    while not (source/'topology.json').exists():
        if stopping or datetime.now(timezone.utc)>=deadline:return
        time.sleep(1)
    for name in ('cluster.json','plan.json','genesis.json','topology.json'):
        shutil.copyfile(source/name,root/name);os.chown(root/name,1000,1000)
    os.chown(root,1000,1000)
    domain=os.environ['DAWL_CONSOLE_HOST']
    if not domain.endswith('.norwayeast.azurecontainer.io') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in domain):raise ValueError('invalid pilot Console domain')
    Path('/tmp/Caddyfile').write_text(domain+' {\n encode gzip\n reverse_proxy 127.0.0.1:8882\n header X-Content-Type-Options nosniff\n}\n')
    def demote():os.setgroups([]);os.setgid(1000);os.setuid(1000)
    try:
        children.append(subprocess.Popen(['python','/app/scripts/hybrid_observer.py'],preexec_fn=demote))
        children.append(subprocess.Popen(['caddy','run','--config','/tmp/Caddyfile','--adapter','caddyfile']))
        while not stopping and datetime.now(timezone.utc)<deadline:
            if any(c.poll() is not None for c in children):raise RuntimeError('Console child exited')
            time.sleep(1)
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
        for child in children:
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:child.kill();child.wait()


if __name__=='__main__':main()
