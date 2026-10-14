"""Replace a stopped pooled pilot with one container per node, within its deadline."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time

from control import REPO,az,save
from launch import command
from hybrid import WSL
sys.path.insert(0,str(REPO/'scripts'))
from node_topology import default_topology,validate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--subscription',required=True)
    p.add_argument('--root',type=Path,default=REPO/'artifacts/azure-hybrid-001')
    p.add_argument('--topology',type=Path)
    p.add_argument('--resume-unstarted',action='store_true',help='Retry rejected provisioning only when no ACI group or installed configuration exists')
    a=p.parse_args();root=a.root.resolve()
    run=json.loads((root/'deployment.json').read_text());window=json.loads((root/'pool-started.json').read_text())
    if run['subscription']!=a.subscription or run['region']!='norwayeast' or run['resource_group']!='DAWL': raise ValueError('Pilot scope differs')
    if (root/'node-deployment.json').exists() and not a.resume_unstarted: raise ValueError('Per-node deployment already attempted; inspect without resetting state')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(window['shutdown_at']): raise ValueError('Original pilot window expired')
    scope=['--subscription',a.subscription,'-g','DAWL']
    if a.resume_unstarted:
        saved=json.loads((root/'node-deployment.json').read_text())
        if saved['subscription']!=a.subscription or saved['pool']!=run['prefix']+'-nodes': raise ValueError('Saved deployment differs')
        if (root/'node-operator-config.json').exists() or (root/'node-outputs.json').exists(): raise ValueError('Cannot resume an installed network')
        groups=az('container','list',*scope)
        if any(g['name']==saved['pool'] for g in groups): raise ValueError('ACI group exists; inspect its signing state instead')
        save(root/'node-rejected-provisioning.json',saved)
    prior=az('container','show',*scope,'-n',run['pool'])
    if prior['instanceView']['state'] not in ('Stopped','Succeeded','Failed'): raise ValueError('Stop baseline first: total Azure CPU must stay within three')
    if not (root/'archive-verified.json').exists(): raise ValueError('Preserve the baseline state first')
    topology=validate(json.loads(a.topology.read_text()) if a.topology else default_topology())
    if topology['gateway']!={'cpu':.5,'memory_gb':.5}: raise ValueError('Template gateway budget differs')
    prefix=run['prefix'];native='/var/lib/dawl/'+prefix+'-nodes'
    save(root/'node-deployment.json',dict(subscription=a.subscription,resource_group='DAWL',pool=prefix+'-nodes',
        native_root=native,shutdown_at=window['shutdown_at'],expires_at=window['expires_at'],topology=topology))
    if a.resume_unstarted:
        images=json.loads((root/'role-images.json').read_text())
    else:
        login=az('acr','login','--subscription',a.subscription,'--name',run['registry'],'--expose-token')
        host=login['loginServer'];auth=native+'-auth.json'
        command(*WSL,'podman','login','--authfile',auth,'--username','00000000-0000-0000-0000-000000000000',
            '--password-stdin',host,data=(login['accessToken']+'\n').encode())
        images={}
        try:
            for role in ('validator','provider','worker','publisher','gateway','observer'):
                print('Publishing role image: '+role,flush=True)
                command(*WSL,'podman','push','--authfile',auth,'localhost/dawl-'+role+':node-v1',host+'/dawl-'+role+':nodes')
                digest=az('acr','repository','show','--subscription',a.subscription,'--name',run['registry'],
                          '--image','dawl-'+role+':nodes','--query','digest')
                images[role]=host+'/dawl-'+role+'@'+digest
        finally: command(*WSL,'podman','logout','--authfile',auth,host)
        save(root/'role-images.json',images)
        command(*WSL,'mkdir','-m','700',native)
        command(*WSL,'ssh-keygen','-q','-t','ed25519','-N','','-f',native+'/operator')
    key=command(*WSL,'cat',native+'/operator.pub').decode().strip()
    roles={k:images[k] for k in ('validator','provider','worker','publisher')}
    parameters=dict(prefix=prefix,location='norwayeast',nodeSpecs=[n for n in topology['nodes'] if n['site']=='cloud'],
        allNodes=topology['nodes'],roleImages=roles,gatewayImage=images['gateway'],operatorPublicKey=key,
        shutdownAt=window['shutdown_at'],expiresAt=window['expires_at'])
    save(root/'node-parameters.json',dict(parameters={k:dict(value=v) for k,v in parameters.items()}))
    template=REPO/'deploy/azure/node-pool.bicep'
    try:
        az('deployment','group','validate',*scope,'--template-file',template,'--parameters','@'+str(root/'node-parameters.json'))
        result=az('deployment','group','create',*scope,'--name',prefix+'-nodes','--template-file',template,'--parameters','@'+str(root/'node-parameters.json'))
        save(root/'node-outputs.json',result['properties']['outputs'])
        pool_id=f'/subscriptions/{a.subscription}/resourceGroups/DAWL/providers/Microsoft.ContainerInstance/containerGroups/{prefix}-nodes'
        host_key=None;end=time.monotonic()+600
        while time.monotonic()<end:
            logs=az('rest','--method','get','--url','https://management.azure.com'+pool_id+'/containers/gateway/logs?api-version=2023-05-01&tail=100')
            host_key=next((line.split(' ',1)[1] for line in logs['content'].splitlines() if line.startswith('DAWL_SSH_HOST_KEY ')),None)
            if host_key: break
            time.sleep(5)
        if not host_key: raise TimeoutError('No API-authenticated gateway host key')
        config=dict(prefix=prefix+'-nodes',host=result['properties']['outputs']['fqdn']['value'],port=2222,
            host_key=host_key,deadline=window['shutdown_at'],local_container=prefix+'-nodes-blueprint-publisher',
            chain_id='hypertrix-'+prefix,role_images=roles,nodes=topology['nodes'],topology=topology)
        save(root/'node-operator-config.json',config)
        command(*WSL,'python3','-c','from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
            native+'/config.json',data=json.dumps(config).encode())
        print(json.dumps(dict(pool=prefix+'-nodes',cloud_containers=len(parameters['nodeSpecs'])+1,cloud_vcpus=3,native_root=native)),flush=True)
    except Exception:
        try: az('container','stop',*scope,'-n',prefix+'-nodes')
        except Exception: pass
        raise


if __name__=='__main__': main()
