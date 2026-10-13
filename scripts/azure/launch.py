"""Bootstrap the prepared AKS pilot with node-local keys and a public network bundle.

Windows workspace + WSL Podman build host. Requires a successful control.py deploy.
Never mount/copy remote validator keys. Refuses an existing DAWL namespace.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

from control import REPO, az, save
from manifests import manifests, members


def command(*args, data=None, timeout=1200):
    result = subprocess.run(list(map(str,args)), input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-3000:])
    return result.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=REPO/'artifacts/azure-pilot-001')
    p.add_argument('--image', default='localhost/dawl-cluster:aks-v1')
    a = p.parse_args()
    source = Path(r'\\wsl.localhost\kali-linux\var\lib\dawl\cluster-v2\provider-0')
    root = a.root.resolve()
    run = json.loads((root/'deployment.json').read_text())
    az('account','set','--subscription',run['subscription'])
    if datetime.now(timezone.utc) >= datetime.fromisoformat(run['expires_at'].replace('Z','+00:00')):
        raise ValueError('pilot deadline has passed; do not restart it')
    if (root/'launch-started.json').exists():
        raise ValueError('launch already attempted; inspect retained state, never reset identities')
    os.environ['PATH'] = str(REPO/'artifacts/azure-tools')+os.pathsep+str(REPO/'artifacts/azure-tools/Scripts')+os.pathsep+os.environ['PATH']
    kube = REPO/'artifacts/azure-tools/kubectl.exe'
    os.environ['KUBECONFIG'] = str(root/'kubeconfig')
    az('aks','get-credentials','--subscription',run['subscription'],'-g',run['resource_group'],'-n',run['cluster'],
       '--file',root/'kubeconfig')
    command(REPO/'artifacts/azure-tools/kubelogin.exe','convert-kubeconfig','-l','azurecli')

    def kubectl(*args, **kwargs):
        return command(kube,*args,**kwargs)

    # Query first and distinguish connectivity/auth failures from an absent namespace.
    namespaces = json.loads(kubectl('get','namespaces','-o','json'))
    if any(n['metadata']['name']=='dawl' for n in namespaces['items']):
        raise ValueError('DAWL namespace already exists; preserve it')
    save(root/'launch-started.json', dict(started_at=datetime.now(timezone.utc).isoformat()))
    login = az('acr','login','--subscription',run['subscription'],'--name',run['registry'],'--expose-token')
    host = login['loginServer']
    wsl = ['wsl','-d','kali-linux','-u','root','--exec']
    authfile = '/var/lib/dawl/'+run['registry']+'-auth.json'
    command(*wsl,'podman','login','--authfile',authfile,'--username','00000000-0000-0000-0000-000000000000',
            '--password-stdin',host,data=(login['accessToken']+'\n').encode())
    try:
        command(*wsl,'podman','push','--authfile',authfile,a.image,host+'/dawl:pilot')
    finally:
        command(*wsl,'podman','logout','--authfile',authfile,host)
    digest = az('acr','repository','show','--subscription',run['subscription'],'--name',run['registry'],
                '--image','dawl:pilot','--query','digest')
    image = host+'/dawl@'+digest
    workload = manifests(20,image)
    save(root/'workloads.json',workload)
    kubectl('apply','-f',root/'workloads.json')
    nodes = members(20)
    public = root/'ceremony'
    (public/'registrations').mkdir(parents=True,exist_ok=False)

    def exec_python(name, code, data=None):
        return kubectl('-n','dawl','exec','-i',name+'-0','--','python','-c',code,data=data)

    def registration(member):
        deadline = time.monotonic()+900
        while time.monotonic() < deadline:
            try:
                data = exec_python(member['name'],"from pathlib import Path; print(Path('/data/node/registration.json').read_text())")
                value = json.loads(data)
                if value['registration']['body']['host'] != member['host']:
                    raise ValueError('unexpected registered Service address')
                save(public/'registrations'/(member['name']+'.json'), value)
                return
            except RuntimeError:
                time.sleep(5)
        raise TimeoutError('pod did not initialize: '+member['name'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(registration,nodes))
    ceremony_wsl = command(*wsl,'wslpath','-u',str(public)).decode().strip()
    assembled = json.loads(command(*wsl,'podman','run','--rm','--network','none',
        '-v',ceremony_wsl+':/ceremony','-v','/var/lib/dawl/cluster-v2/provider-0:/template:ro',
        '--entrypoint','python',a.image,'/app/dawl_external.py','assemble','--root','/ceremony/network',
        '--template','/template','--chain-id','dawl-azure-'+run['registry'],'--image',image,'--registrations',
        *['/ceremony/registrations/'+n['name']+'.json' for n in nodes]))
    bundle = public/'network'
    config = dict(apiVersion='v1',kind='ConfigMap',metadata=dict(name='bootstrap',namespace='dawl'),
                  data={f.name:f.read_text() for f in bundle.iterdir()})
    config['data']['network.sha256'] = assembled['network_sha256']
    save(root/'bootstrap.json',config)
    save(root/'network-pins.json',dict(**assembled,image=image))
    kubectl('apply','-f',root/'bootstrap.json')
    # Start validators/publisher before workers. ConfigMap propagation may take a minute.
    for member in nodes:
        if member['role'] in ('validator','publisher'):
            exec_python(member['name'],"from pathlib import Path; Path('/data/start').touch()")
    index = json.loads((source/'artifact-index.json').read_text())
    archive = root/'public-objects.tar'
    with tarfile.open(archive,'w') as tar:
        for digest, descriptor in sorted(index.items()):
            path = source/'objects'/digest
            if digest != descriptor['sha256'] or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('source artifact digest mismatch')
            tar.add(path,arcname=digest,recursive=False)
    def seed(member):
        name=member['name']
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            try:
                exec_python(name,"from pathlib import Path; assert Path('/data/node/installed.json').exists()")
                break
            except RuntimeError: time.sleep(3)
        else: raise TimeoutError('provider install did not finish')
        # Stream only the allowlisted public objects, then verify again inside the node.
        with archive.open('rb') as stream:
            result=subprocess.run([str(kube),'-n','dawl','exec','-i',name+'-0','--','python','-c',
                "import sys,tarfile; from pathlib import Path; p=Path('/data/seed'); p.mkdir(exist_ok=True); "
                "tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall(p,filter='data')"],stdin=stream,capture_output=True)
        if result.returncode: raise RuntimeError(result.stderr.decode(errors='replace'))
        kubectl('-n','dawl','exec',name+'-0','--','python','/app/dawl_external.py','seed-provider',
                '--root','/data/node','--objects','/data/seed')
        exec_python(name,"from pathlib import Path; Path('/data/provider-ready').touch()")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(seed,[n for n in nodes if n['role']=='provider']))
    kubectl('-n','dawl','wait','--for=condition=Ready','pod','-l','dawl-role=validator','--timeout=300s')
    kubectl('-n','dawl','wait','--for=condition=Ready','pod','-l','dawl-role=provider','--timeout=300s')
    # One certified read before authorizing the worker start gates.
    status=json.loads(kubectl('-n','dawl','exec','publisher-0','--','python','/app/dawl_cluster.py','status','--root','/data/node'))
    save(root/'before-workers.json',status)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(run['expires_at'].replace('Z','+00:00')):
        raise TimeoutError('pilot deadline reached during setup; do not start workers')
    start=datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n:exec_python(n['name'],"from pathlib import Path; Path('/data/start').touch()"),
                      [n for n in nodes if n['role']=='worker']))
    save(root/'launched.json',dict(started_at=start,workers=20,image=image,network_sha256=assembled['network_sha256']))
    print('Twenty auditors started; use observe.py to record completion and certificates.')


if __name__ == '__main__': main()
