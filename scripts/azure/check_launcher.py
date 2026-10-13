"""Local Podman lifecycle probe for the AKS launcher. Never selects live DAWL nodes."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


def run(*args):
    return subprocess.check_output(['podman',*args],text=True)


with tempfile.TemporaryDirectory(prefix='dawl-aks-probe-') as folder:
    root=Path(folder)
    (root/'data').mkdir();(root/'bootstrap').mkdir()
    import os
    os.chmod(root,0o755);os.chown(root/'data',1000,1000)
    name='dawl-aks-launcher-probe-'+str(os.getpid())
    options=['run','-d','--name',name,'--network','none','--read-only','--cap-drop','ALL',
             '-v',str(root/'data')+':/data','-v',str(root/'bootstrap')+':/bootstrap:ro',
             '--tmpfs','/tmp:size=16m','-e','DAWL_NAME=probe','-e','DAWL_ROLE=worker',
             '-e','DAWL_HOST=10.240.1.40','--entrypoint','python','localhost/dawl-cluster:aks-v1',
             '/app/scripts/kubernetes_node.py']
    try:
        run(*options)
        for _ in range(50):
            if (root/'data/node/registration.json').exists(): break
            time.sleep(.1)
        else: raise TimeoutError('launcher registration missing')
        first=hashlib.sha256((root/'data/node/key.private.pem').read_bytes()).hexdigest()
        started=time.monotonic();run('stop','--time','5',name)
        info=json.loads(run('inspect',name))[0]
        assert info['State']['ExitCode']==0,info['State']
        elapsed=time.monotonic()-started
        run('start',name);time.sleep(1);run('stop','--time','5',name)
        assert first==hashlib.sha256((root/'data/node/key.private.pem').read_bytes()).hexdigest()
        assert json.loads(run('inspect',name))[0]['State']['ExitCode']==0
        print(json.dumps(dict(registration_created=True,key_preserved_on_restart=True,
                             stopped_without_sigkill=True,stop_seconds=round(elapsed,3),
                             scope='No network/bootstrap or cloud execution in this lifecycle probe')))
    finally:
        subprocess.run(['podman','rm','-f',name],capture_output=True)
