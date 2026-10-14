"""Three-vCPU ACI pilot in DAWL/Norway East, paired with the WSL operator.

Provisioning is restricted to a recorded, uniquely named pilot in the supplied
subscription. The existing resource group and unrelated resources are preserved.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import time

from control import REPO, az, save
from launch import command
from prices import query

WSL=['wsl','-d','kali-linux','-u','root','--exec']
IMAGE='localhost/dawl-cluster:hybrid-v1'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','deploy','status','stop','export-closed-state','cleanup'])
    p.add_argument('--subscription',required=True)
    p.add_argument('--root',type=Path,default=REPO/'artifacts/azure-hybrid-001')
    a=p.parse_args();root=a.root.resolve()
    account=az('account','show','--subscription',a.subscription)
    if account['id'].lower()!=a.subscription.lower() or account['state']!='Enabled':
        raise ValueError('Exact enabled subscription required')
    scope=['--subscription',a.subscription,'--resource-group','DAWL']
    if a.command=='prepare':
        if (root/'deployment.json').exists(): raise ValueError('Pilot already recorded; inspect it')
        group=az('group','show','--subscription',a.subscription,'--name','DAWL')
        if group['location']!='norwayeast': raise ValueError('DAWL must be in Norway East')
        rows=query("serviceName eq 'Container Instances' and armRegionName eq 'norwayeast' and priceType eq 'Consumption'")
        def meter(name,unit):
            eligible=[r for r in rows if r['meterName']==name and r['unitOfMeasure']==unit and r['skuName']=='Standard']
            if not eligible: raise ValueError('Missing ACI meter')
            return max(eligible,key=lambda r:r['retailPrice'])
        cpu=meter('Standard vCPU Duration','1 Hour');memory=meter('Standard Memory Duration','1 GB Hour')
        compute=4*(3*cpu['retailPrice']+8*memory['retailPrice'])
        quote=dict(fetched_at=datetime.now(timezone.utc).isoformat(),currency='EUR',cloud_vcpus=3,memory_gb=8,
            hours=4,compute_eur=compute,ancillary_allowance_eur=5,estimated_envelope_eur=round((compute*1.25+5)*1.25,2),
            approved_max_eur=50,meters=[cpu,memory],
            assumptions=['Ancillary allowance covers Basic ACR, small Azure Files share, Logic App and bounded transfer',
                'Includes 25 percent compute contingency and 25 percent tax allowance; not a billing hard cap',
                'Only this pilot is scoped; existing subscription spend is excluded'])
        if quote['estimated_envelope_eur']>50: raise ValueError('Approved budget exceeded')
        save(root/'estimate.json',quote)
        prefix='dawlh'+datetime.now(timezone.utc).strftime('%m%d%H%M%S')
        run=dict(subscription=a.subscription,resource_group='DAWL',region='norwayeast',prefix=prefix,
                 registry=prefix+'acr',storage=prefix+'state',pool=prefix+'-pool',deadline_workflow=prefix+'-deadline',
                 native_root='/var/lib/dawl/'+prefix,cloud_vcpus=3,budget_eur=50)
        save(root/'deployment.json',run)
        params=['prefix='+prefix,'location=norwayeast']
        template=REPO/'deploy/azure/hybrid-foundation.bicep'
        az('deployment','group','validate',*scope,'--template-file',template,'--parameters',*params)
        result=az('deployment','group','create',*scope,'--name',prefix+'-foundation','--template-file',template,'--parameters',*params)
        save(root/'foundation.json',result['properties']['outputs'])
        print(json.dumps(dict(prefix=prefix,estimate_eur=quote['estimated_envelope_eur'],cloud_vcpus=3)))
        return
    run=json.loads((root/'deployment.json').read_text())
    if run['subscription']!=a.subscription or run['resource_group']!='DAWL' or run['region']!='norwayeast' or run['cloud_vcpus']!=3:
        raise ValueError('Saved scope differs from requested hybrid pilot')
    if a.command=='deploy':
        if (root/'pool-started.json').exists(): raise ValueError('Cloud pool creation already attempted; never reset signing state')
        quote=json.loads((root/'estimate.json').read_text())
        if datetime.now(timezone.utc)-datetime.fromisoformat(quote['fetched_at'])>timedelta(hours=2):
            raise ValueError('Refresh the estimate before deployment')
        login=az('acr','login','--subscription',a.subscription,'--name',run['registry'],'--expose-token')
        host=login['loginServer'];authfile=run['native_root']+'-auth.json'
        command(*WSL,'podman','login','--authfile',authfile,'--username','00000000-0000-0000-0000-000000000000',
            '--password-stdin',host,data=(login['accessToken']+'\n').encode())
        print('Uploading the tested image to the private registry.',flush=True)
        try: command(*WSL,'podman','push','--authfile',authfile,IMAGE,host+'/dawl:hybrid')
        finally: command(*WSL,'podman','logout','--authfile',authfile,host)
        digest=az('acr','repository','show','--subscription',a.subscription,'--name',run['registry'],
                  '--image','dawl:hybrid','--query','digest')
        image=host+'/dawl@'+digest
        native=run['native_root']
        command(*WSL,'mkdir','-m','700',native)
        command(*WSL,'ssh-keygen','-q','-t','ed25519','-N','','-f',native+'/operator')
        public=command(*WSL,'cat',native+'/operator.pub').decode().strip()
        now=datetime.now(timezone.utc)
        shutdown=(now+timedelta(hours=3,minutes=40)).isoformat()
        expires=(now+timedelta(hours=3,minutes=50)).isoformat()
        save(root/'pool-started.json',dict(started_at=now.isoformat(),shutdown_at=shutdown,expires_at=expires,image=image))
        params={k:dict(value=v) for k,v in dict(prefix=run['prefix'],image=image,operatorPublicKey=public,
            expiresAt=expires,shutdownAt=shutdown,location='norwayeast').items()}
        save(root/'pool-parameters.json',dict(parameters=params))
        template=REPO/'deploy/azure/hybrid-pool.bicep'
        try:
            az('deployment','group','validate',*scope,'--template-file',template,'--parameters','@'+str(root/'pool-parameters.json'))
            result=az('deployment','group','create',*scope,'--name',run['prefix']+'-network','--template-file',template,
                      '--parameters','@'+str(root/'pool-parameters.json'))
            save(root/'pool-outputs.json',result['properties']['outputs'])
            # API-authenticated logs anchor SSH host identity; never trust ssh-keyscan alone.
            end=time.monotonic()+600
            key=None
            pool_id=f'/subscriptions/{a.subscription}/resourceGroups/DAWL/providers/Microsoft.ContainerInstance/containerGroups/'+run['pool']
            while time.monotonic()<end:
                logs=az('rest','--method','get','--url','https://management.azure.com'+pool_id+'/containers/pool/logs?api-version=2023-05-01&tail=100')
                key=next((line.split(' ',1)[1] for line in logs['content'].splitlines() if line.startswith('DAWL_SSH_HOST_KEY ')),None)
                if key: break
                time.sleep(5)
            if not key: raise TimeoutError('Cloud pool did not expose its SSH host key in authenticated logs')
            config=dict(host=result['properties']['outputs']['fqdn']['value'],port=2222,host_key=key,
                        deadline=shutdown,local_container=run['prefix']+'-local',chain_id=run['prefix'],image_pin=image)
            save(root/'operator-config.json',config)
            command(*WSL,'python3','-c','from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
                    native+'/config.json',data=json.dumps(config).encode())
            print(json.dumps(dict(fqdn=config['host'],native_root=native,shutdown_at=shutdown,cloud_vcpus=3)),flush=True)
        except Exception:
            # A failed deployment must not leave uncontrolled compute. Do not remove state.
            try: az('container','stop',*scope,'--name',run['pool'])
            except Exception: pass
            raise
        return
    group_id=f'/subscriptions/{a.subscription}/resourceGroups/DAWL'
    pool_id=group_id+'/providers/Microsoft.ContainerInstance/containerGroups/'+run['pool']
    if a.command=='status':
        state=az('container','show',*scope,'--name',run['pool'])
        print(json.dumps({k:state.get(k) for k in ('name','location','provisioningState','instanceView','ipAddress','containers')},indent=2))
    elif a.command=='stop':
        az('container','stop',*scope,'--name',run['pool'])
        print('Only the recorded cloud pool was stopped; storage and registry retained.')
    elif a.command=='export-closed-state':
        # Data-plane credential remains in memory and subprocess environment, never output.
        import os
        from control import TOOLS
        keys=az('storage','account','keys','list',*scope,'--account-name',run['storage'])
        env=os.environ.copy();env['AZURE_STORAGE_KEY']=keys[0]['value']
        archive=root/'private-state.tar'
        subprocess.run([str(TOOLS),'-m','azure.cli','storage','file','download','--account-name',run['storage'],
            '--share-name','pilot-state','--path','private-state.tar','--dest',str(archive),'--no-progress','--only-show-errors'],
            env=env,check=True,capture_output=True)
        import hashlib
        save(root/'archive-verified.json',dict(path=str(archive),bytes=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest()))
        print('Private closed-state archive saved locally; do not publish it.')
    elif a.command=='cleanup':
        if not (root/'archive-verified.json').exists(): raise ValueError('Export closed signing state before deleting the pilot')
        resources=az('resource','list',*scope,'--tag','pilot='+run['prefix'])
        allowed={run['registry'],run['storage'],run['pool'],run['deadline_workflow'],run['prefix']+'-pull'}
        if any(r['name'] not in allowed or r['resourceGroup']!='DAWL' for r in resources):
            raise ValueError('Unexpected cleanup scope')
        # Delete compute first, then workflow and supporting resources. Keep DAWL itself.
        ordered=sorted(resources,key=lambda r:0 if r['name']==run['pool'] else 1 if r['name']==run['deadline_workflow'] else 2)
        for resource in ordered: az('resource','delete','--subscription',a.subscription,'--ids',resource['id'])
        save(root/'cleaned-up.json',dict(deleted=[r['id'] for r in ordered],at=datetime.now(timezone.utc).isoformat()))
        print('Exact pilot resources removed; DAWL and unrelated resources preserved.')


if __name__=='__main__': main()
