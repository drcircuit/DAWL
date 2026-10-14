"""Inspect, gracefully stop, or export closed state from the recorded per-node pilot."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time

from control import REPO,TOOLS,az,save
from hybrid import WSL
from launch import command


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['status','stop','export-closed-state'])
    parser.add_argument('--subscription',required=True)
    parser.add_argument('--root',type=Path,default=REPO/'artifacts/azure-hybrid-001')
    args=parser.parse_args();root=args.root.resolve()
    original=json.loads((root/'deployment.json').read_text())
    run=json.loads((root/'node-deployment.json').read_text())
    if (args.subscription!=original['subscription'] or args.subscription!=run['subscription']
        or run['resource_group']!='DAWL' or run['pool']!=original['prefix']+'-nodes'):
        raise ValueError('Recorded pilot scope differs')
    scope=['--subscription',args.subscription,'-g','DAWL']
    cloud=[n['name'] for n in run['topology']['nodes'] if n['site']=='cloud']
    upgraded=(root/'contribution-operator-config.json').exists()
    def state():
        value=az('container','show',*scope,'-n',run['pool'])
        if value['tags'].get('pilot')!=original['prefix']: raise ValueError('Resource ownership tag differs')
        if {n['name'] for n in value['containers']}!=set(cloud)|({'gateway','console'} if upgraded else {'gateway'}): raise ValueError('Unexpected container roster')
        return value
    if args.command=='status':
        value=state()
        print(json.dumps(dict(pool=run['pool'],state=value['instanceView']['state'],
            cpu_requests=sum(n['resources']['requests']['cpu'] for n in value['containers']),
            containers=[dict(name=n['name'],state=n['instanceView']['currentState']) for n in value['containers']]),indent=2))
    elif args.command=='stop':
        value=state()
        if value['instanceView']['state']=='Stopped': return
        # Ask each node to close its own database and archive before stopping ACI.
        code="import sys; from pathlib import Path; sys.path.insert(0,'scripts'); from node_operator import NodeOperator; NodeOperator(Path(sys.argv[1])).gate('stop')"
        command(*WSL,'python3','-c',code,run['native_root'])
        if upgraded:
            command(*WSL,'podman','stop','--time','60',run['pool']+'-cortex-scifi')
        end=time.monotonic()+150
        while time.monotonic()<end:
            value=state()
            nodes=[n for n in value['containers'] if n['name'] in cloud]
            if all(n['instanceView']['currentState']['state']=='Terminated' for n in nodes): break
            time.sleep(5)
        else: raise TimeoutError('Nodes have not all closed; inspect their logs before forcing group stop')
        az('container','stop',*scope,'-n',run['pool'])
        save(root/'node-stopped.json',dict(at=datetime.now(timezone.utc).isoformat(),pool=run['pool']))
        print('Recorded node group stopped; local nodes, registry and archives retained.')
    else:
        keys=az('storage','account','keys','list',*scope,'--account-name',original['storage'])
        environment=os.environ.copy();environment['AZURE_STORAGE_KEY']=keys[0]['value']
        destination=root/('contribution-private-archives' if upgraded else 'node-private-archives')
        if destination.exists():
            destination=root/(destination.name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
        destination.mkdir(exist_ok=False)
        evidence=[]
        for name in cloud:
            target=destination/(name+'.tar')
            if target.exists(): raise ValueError('An export already exists; preserve it before another export')
            subprocess.run([str(TOOLS),'-m','azure.cli','storage','file','download','--account-name',original['storage'],
                '--share-name','nodes-'+name,'--path','private-state.tar','--dest',str(target),
                '--no-progress','--only-show-errors'],env=environment,check=True,capture_output=True)
            # Validate without extraction; private bytes are never printed.
            with tarfile.open(target) as archive:
                names=archive.getnames()
                if not {'key.private.pem','registration.json','installed.json'}.issubset(names):
                    raise ValueError('Incomplete closed archive for '+name)
                for member in archive:
                    if member.isfile():
                        stream=archive.extractfile(member)
                        while stream.read(1024*1024): pass
            with target.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            evidence.append(dict(name=name,path=str(target),bytes=target.stat().st_size,sha256=digest))
        report=dict(at=datetime.now(timezone.utc).isoformat(),archives=evidence)
        save(destination/'manifest.json',report)
        save(root/('contribution-archives-verified.json' if upgraded else 'node-archives-verified.json'),report)
        print('All seven private archives exported and read-verified. Do not publish them.')


if __name__=='__main__': main()
