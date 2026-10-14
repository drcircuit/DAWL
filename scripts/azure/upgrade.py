"""Preserve a closed pilot chain while enabling contribution discovery and HTTPS."""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import time
from control import REPO,TOOLS,az,save
from launch import command
from hybrid import WSL
from prices import query


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['publish-images','deploy','connect-provisioning'])
    parser.add_argument('--root',type=Path,default=REPO/'artifacts/azure-hybrid-001')
    parser.add_argument('--ssh-only',action='store_true',help='Isolate initial transport provisioning before enabling public Console ingress')
    roles=('validator','provider','worker','publisher','gateway','observer','contributor','console')
    parser.add_argument('--roles',nargs='+',choices=roles,default=roles)
    args=parser.parse_args();root=args.root
    run=json.loads((root/'deployment.json').read_text());old=json.loads((root/'node-operator-config.json').read_text())
    if run['subscription']!='58eadcc1-71bf-4547-bd13-7f68a6042a0f' or run['resource_group']!='DAWL' or run['region']!='norwayeast':raise ValueError('pilot scope differs')
    native='/var/lib/dawl/'+run['prefix']+'-nodes';scope=['--subscription',run['subscription'],'-g','DAWL']
    if args.command=='publish-images':
        window=json.loads((root/'pool-started.json').read_text());start=datetime.fromisoformat(window['started_at'])
        shutdown=start+timedelta(hours=23,minutes=40);expires=start+timedelta(hours=23,minutes=50)
        if datetime.now(timezone.utc)>=shutdown:raise ValueError('24-hour total window expired')
        rows=query("serviceName eq 'Container Instances' and armRegionName eq 'norwayeast' and priceType eq 'Consumption'")
        cpu=max(r['retailPrice'] for r in rows if r['meterName']=='Standard vCPU Duration' and r['skuName']=='Standard' and r['unitOfMeasure']=='1 Hour')
        memory=max(r['retailPrice'] for r in rows if r['meterName']=='Standard Memory Duration' and r['skuName']=='Standard' and r['unitOfMeasure']=='1 GB Hour')
        compute=24*(3*cpu+8*memory);envelope=round((compute*1.25+15)*1.25,2)
        if envelope>50:raise ValueError('24-hour estimate exceeds user budget')
        save(root/'contribution-estimate.json',dict(fetched_at=datetime.now(timezone.utc).isoformat(),currency='EUR',hours_total=24,
            compute_eur=compute,ancillary_allowance_eur=15,estimated_envelope_eur=envelope,approved_max_eur=50,
            cpu_hour_eur=cpu,gb_hour_eur=memory,shutdown_at=shutdown.isoformat(),expires_at=expires.isoformat(),
            assumptions=['Includes 25% compute contingency plus 25% tax allowance','Ancillary allowance includes registry, archives, Logic App, transfer and earlier run within the same total window','Operational estimate and timed stop, not an Azure hard billing cap']))
        login=az('acr','login','--subscription',run['subscription'],'--name',run['registry'],'--expose-token');host=login['loginServer'];auth=native+'-auth.json'
        command(*WSL,'podman','login','--authfile',auth,'--username','00000000-0000-0000-0000-000000000000','--password-stdin',host,data=(login['accessToken']+'\n').encode())
        images=json.loads((root/'contribution-images.json').read_text()) if (root/'contribution-images.json').exists() else {}
        try:
            for role in args.roles:
                print('Publishing '+role,flush=True)
                command(*WSL,'podman','push','--authfile',auth,'localhost/dawl-'+role+':node-v2',host+'/dawl-'+role+':contributions')
                digest=az('acr','repository','show','--subscription',run['subscription'],'--name',run['registry'],'--image','dawl-'+role+':contributions','--query','digest')
                images[role]=host+'/dawl-'+role+'@'+digest
        finally:command(*WSL,'podman','logout','--authfile',auth,host)
        if set(images)!=set(roles):raise ValueError('Incomplete role image set')
        save(root/'contribution-images.json',images)
        code="from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
        package=dict(images=images,deadline=shutdown.isoformat(),expires_at=expires.isoformat())
        command(*WSL,'python3','-c',code,native+'/upgrade-package.json',data=json.dumps(package).encode())
        print(json.dumps(dict(estimated_envelope_eur=envelope,shutdown_at=shutdown.isoformat())),flush=True)
        return
    groups=az('container','list',*scope)
    pool=next((g for g in groups if g['name']==run['prefix']+'-nodes'),None)
    if pool is not None:pool=az('container','show',*scope,'-n',run['prefix']+'-nodes')
    if pool is None:
        if args.command!='deploy' or not (root/'recreate-fence.json').exists():raise ValueError('Missing group requires an explicit verified recreation fence')
        fence=json.loads((root/'recreate-fence.json').read_text())
        if not fence['old_group_removed'] or fence['pool']!=run['prefix']+'-nodes':raise ValueError('Invalid recreation fence')
        pool={'id':f"/subscriptions/{run['subscription']}/resourceGroups/DAWL/providers/Microsoft.ContainerInstance/containerGroups/{run['prefix']}-nodes"}
    elif args.command=='deploy':
        raise ValueError('Resource allocation changes require closing, exporting and deleting the old group before recreation')
    baseline=az('container','show',*scope,'-n',run['pool'])
    if baseline['instanceView']['state']!='Stopped':raise ValueError('Old pooled group must stay stopped')
    evidence=json.loads((root/('pre-recreate-archives.json' if (root/'recreate-fence.json').exists() else 'node-archives-verified.json')).read_text())
    if datetime.now(timezone.utc)-datetime.fromisoformat(evidence['at'])>timedelta(hours=1):raise ValueError('Closed archive export is stale')
    images=json.loads((root/'contribution-images.json').read_text());quote=json.loads((root/'contribution-estimate.json').read_text())
    if datetime.now(timezone.utc)>=datetime.fromisoformat(quote['shutdown_at']):raise ValueError('deadline expired')
    specs=[dict(n) for n in old['nodes'] if n['site']=='cloud']
    for node in specs:
        if node['role']=='worker':node['memory_gb']=1.1
    hashes={a['name']:a['sha256'] for a in evidence['archives']}
    if set(hashes)!={n['name'] for n in specs}:raise ValueError('closed archives do not cover every cloud node')
    key=command(*WSL,'cat',native+'/operator.pub').decode().strip()
    parameters=dict(prefix=run['prefix'],dnsLabel=run['prefix']+'-collective',exposeConsole=not args.ssh_only,location='norwayeast',nodeSpecs=specs,allNodes=old['nodes'],
        roleImages={k:images[k] for k in ('validator','provider','worker','publisher')},gatewayImage=images['gateway'],consoleImage=images['console'],
        operatorPublicKey=key,restoreHashes=hashes,shutdownAt=quote['shutdown_at'],expiresAt=quote['expires_at'])
    save(root/'contribution-parameters.json',dict(parameters={k:dict(value=v) for k,v in parameters.items()}))
    template=REPO/'deploy/azure/contribution-pool.bicep'
    if args.command=='deploy':
        az('deployment','group','validate',*scope,'--template-file',template,'--parameters','@'+str(root/'contribution-parameters.json'))
        deployment_name=run['prefix']+'-contributions-'+datetime.now(timezone.utc).strftime('%H%M%S')
        save(root/'active-contribution-deployment.json',dict(name=deployment_name))
        az('deployment','group','create',*scope,'--name',deployment_name,'--template-file',template,'--parameters','@'+str(root/'contribution-parameters.json'),'--no-wait')
        for _ in range(40):
            result=az('deployment','group','show',*scope,'--name',deployment_name)
            status=result['properties']['provisioningState']
            if status=='Succeeded':break
            if status in ('Failed','Canceled'):
                raise RuntimeError('Deployment '+status+': '+json.dumps(result['properties'].get('error')))
            time.sleep(15)
        else:raise TimeoutError('Deployment remains pending; inspect the existing group before retrying')
        outputs=result['properties']['outputs']
        save(root/'contribution-outputs.json',outputs)
    else:
        expected={**{n['name']:images[n['role']] for n in specs},'gateway':images['gateway'],'console':images['console']}
        if {c['name']:c['image'] for c in pool['containers']}!=expected or any(c['instanceView']['currentState']['state']!='Running' for c in pool['containers']):
            raise ValueError('Every upgraded container must be running at its exact approved image before connecting')
        outputs={'fqdn':{'value':pool['ipAddress']['fqdn']}}
    hostkey=None
    for _ in range(120):
        try:
            logs=az('rest','--method','get','--url','https://management.azure.com'+pool['id']+'/containers/gateway/logs?api-version=2023-05-01&tail=100')['content']
            hostkey=next((l.split(' ',1)[1] for l in logs.splitlines() if l.startswith('DAWL_SSH_HOST_KEY ')),None)
        except RuntimeError as error:
            if 'ContainerGroupDeploymentNotReady' not in str(error):raise
            # Still authenticated through Azure management; never learn a host key from the untrusted SSH network.
            output=command(TOOLS,'-m','azure.cli','container','exec',*scope,'-n',run['prefix']+'-nodes',
                '--container-name','gateway','--exec-command','cat /run/dawl-ssh/host.pub','--only-show-errors').decode().strip()
            hostkey=next((line for line in output.splitlines() if line.startswith('ssh-ed25519 ')),None)
        if hostkey:break
        time.sleep(3)
    if not hostkey:raise RuntimeError('No authenticated gateway host key')
    config={**old,'host_key':hostkey,'host':outputs['fqdn']['value'],'role_images':parameters['roleImages'],
        'deadline':quote['shutdown_at'],'discovery':True,'all_images':images}
    config['topology']={**config['topology'],'nodes':[next((s for s in specs if s['name']==n['name']),n) for n in old['nodes']],
        'gateway':dict(cpu=.3,memory_gb=.4),'console':dict(cpu=.2,memory_gb=.5)}
    config['nodes']=config['topology']['nodes']
    save(root/'contribution-operator-config.json',config)
    command(*WSL,'python3','-c',"from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())",native+'/config-upgrade.json',data=json.dumps(config).encode())
    print('Running cloud containers checked and gateway host key pinned; public connectivity and chain resume still require verification.',flush=True)


if __name__=='__main__':main()
