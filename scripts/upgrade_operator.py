"""WSL side of the same-chain contribution pilot upgrade. Never creates validator keys."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
from hybrid_operator import run,save,wait_for,SOURCE
from node_operator import NodeOperator


def cli(operator,source):
    return json.loads(run('podman','exec','-i',operator.container(operator.publisher),'python','-c',
        "import sys;sys.path.insert(0,'/app/src');"+source))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','stage-public','resume','activate','contributor','publish','report'])
    parser.add_argument('--root',type=Path,default=Path('/var/lib/dawl/dawlh1013185918-nodes'))
    args=parser.parse_args();root=args.root;operator=NodeOperator(root)
    package=json.loads((root/'upgrade-package.json').read_text())
    if args.command=='stage-public':
        config=json.loads((root/'config-upgrade.json').read_text())
        observer=root/'exchange/observer';observer.mkdir(exist_ok=True)
        for name in ('cluster.json','plan.json','genesis.json'):
            shutil.copyfile(root/'nodes'/operator.publisher/'private/state'/name,observer/name)
        save(observer/'topology.json',config['topology'])
        with tarfile.open(root/'upgrade-public.tar.gz','w:gz') as tar:
            for name in ('bundle','runtime-upgrade.json','observer','network.sha256'):
                tar.add(root/'exchange'/name,arcname=name)
        print((root/'upgrade-public.tar.gz').stat().st_size)
        return
    if args.command=='prepare':
        before=operator.local_cli('status');save(root/'before-upgrade.json',before)
        if before['complete']!=before['jobs'] or before['leases'] or not before['dataset_release']:raise ValueError('baseline must be released and idle')
        digest=run('podman','run','--rm','--network','none','--entrypoint','python','localhost/dawl-validator:node-v2','-c',
            "import sys;sys.path.insert(0,'/app/src');from dawl_platform.workflow import implementation_digest;print(implementation_digest())").decode().strip()
        body=dict(schema='hypertrix.runtime-upgrade/1',network_sha256=(root/'exchange/network.sha256').read_text().strip(),
            implementation_sha256=digest,role_images={k:package['images'][k] for k in ('validator','provider','worker','publisher')},
            prior_role_images=operator.config['role_images'],deadline=package['deadline'],reason='DHT role discovery and independently replayed append-only contributions')
        code="from dawl_node.common import sign,load_key;import json;from pathlib import Path;print(json.dumps(sign(json.loads(sys.stdin.read()),load_key(Path('/node/state/key.private.pem')))))"
        signed=json.loads(run('podman','exec','-i',operator.container(operator.publisher),'python','-c',"import sys;sys.path.insert(0,'/app/src');"+code,data=json.dumps(body).encode()))
        save(root/'exchange/runtime-upgrade.json',signed);os.chmod(root/'exchange/runtime-upgrade.json',0o644)
        save(root/'runtime-upgrade.json',signed)
        print('Signed runtime pins; prior Blueprint '+before['dataset_release']['id'],flush=True)
        return
    if args.command=='resume':
        # The Azure command has already fenced the old group and verified/exported every closed archive.
        upgraded=json.loads((root/'config-upgrade.json').read_text())
        if not (root/'config-pre-upgrade.json').exists():shutil.copyfile(root/'config.json',root/'config-pre-upgrade.json')
        try:operator.close_tunnel()
        except RuntimeError:pass
        save(root/'config.json',upgraded);operator=NodeOperator(root)
        (root/'known_hosts').write_text(f"[{upgraded['host']}]:{upgraded['port']} "+upgraded['host_key']+'\n')
        operator.remote('true')
        # Copy only public bootstrap material, never any node's private signing volume.
        observer=root/'exchange/observer';observer.mkdir(exist_ok=True)
        for name in ('cluster.json','plan.json','genesis.json'):
            shutil.copyfile(root/'nodes'/operator.publisher/'private/state'/name,observer/name)
        save(observer/'topology.json',upgraded['topology'])
        public=root/'upgrade-public.tar'
        with tarfile.open(public,'w') as tar:
            for name in ('bundle','network.sha256','runtime-upgrade.json','observer'):
                tar.add(root/'exchange'/name,arcname=name)
        operator.remote('tar','-xf','-','-C','/exchange',data=public.read_bytes())
        operator.remote('chmod','-R','a+rX','/exchange/bundle','/exchange/observer','/exchange/network.sha256','/exchange/runtime-upgrade.json')
        operator.tunnel()
        local=[n for n in upgraded['nodes'] if n['site']=='local']
        for node in local:
            name=operator.container(node['name']);state=json.loads(run('podman','inspect',name))[0]['State']
            if state['Running']:raise ValueError('Old local signer is still running: '+name)
            if not (root/'nodes'/node['name']/'archive/stopped.json').exists():raise ValueError('Local node did not close its state')
        (root/'exchange/stop').unlink(missing_ok=True)
        for node in local:
            name=operator.container(node['name']);run('podman','rename',name,name+'-pre-upgrade')
            base=root/'nodes'/node['name']
            command=['podman','run','-d','--name',name,'--network','host','--pids-limit','256','--cpus',str(node['cpu']),'--memory',str(node['memory_gb'])+'g',
                '-v',str(base/'private')+':/node','-v',str(base/'archive')+':/persist','-v',str(root/'exchange')+':/exchange']
            env=dict(DAWL_NAME=node['name'],DAWL_HOST=node['host'],DAWL_SITE='local',DAWL_DEADLINE=upgraded['deadline'],DAWL_DHT='1',DAWL_UPGRADE='1',
                DAWL_IMAGE_PIN=upgraded['role_images'][node['role']])
            if node['role']=='provider':command+=['-v',str(SOURCE/'objects')+':/seed:ro'];env['DAWL_SEED_OBJECTS']='/seed'
            for k,v in env.items():command+=['-e',k+'='+v]
            run(*command,'localhost/dawl-'+node['role']+':node-v2')
        observer_name=operator.container('observer');run('podman','stop','--time','20',observer_name);run('podman','rename',observer_name,observer_name+'-pre-upgrade')
        save(root/'observer/topology.json',upgraded['topology']);os.chown(root/'observer/topology.json',1000,1000)
        run('podman','run','-d','--name',observer_name,'--network','host','--cpus','1','--memory','1g','--cap-drop','ALL','--security-opt','no-new-privileges',
            '-v',str(root/'observer')+':/observer','-v',str(root/'exchange')+':/exchange:ro','localhost/dawl-observer:node-v2')
        # Cloud provider caches were ephemeral: seed from the original pinned public object set.
        operator.remote('mkdir','-p','/exchange/seed')
        import subprocess
        with (root/'public-objects.tar').open('rb') as stream:
            result=subprocess.run([*operator.ssh_base,operator.target,'tar -xf - -C /exchange/seed'],stdin=stream,capture_output=True,timeout=600)
        if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace'))
        operator.remote('chmod','-R','a+rX','/exchange/seed')
        for gate in ('start','seed-ready','start-workers'):operator.gate(gate)
        state=wait_for(lambda:operator.local_cli('status'),240);save(root/'after-upgrade-before-activation.json',state)
        before=json.loads((root/'before-upgrade.json').read_text())
        if state['dataset_release']!=before['dataset_release'] or state['chain_id']!=before['chain_id'] or state['plan_sha256']!=before['plan_sha256'] or state['height']<before['height']:
            raise ValueError('Upgrade failed continuity check')
        print('Same chain resumed; old Blueprint and plan retained at height '+str(state['height']),flush=True)
        return
    if args.command=='activate':
        upgrade=json.loads((root/'runtime-upgrade.json').read_text());trust=json.loads((root/'exchange/bundle/trust.json').read_text())
        payload=dict(version=2,implementation_sha256=upgrade['body']['implementation_sha256'],runtime_manifest=upgrade['id'],
            tokenizer_sha256=trust['anchors']['tokenizer_sha256'],embedding_sha256=trust['anchors']['embedding_sha256'])
        code="import asyncio,json;from pathlib import Path;from dawl_cluster.client import Client;from dawl_node.common import load_key;c=Client(Path('/node/state'),load_key(Path('/node/state/key.private.pem')));print(json.dumps(asyncio.run(c.command('activate_contributions',"+repr(payload)+"))))"
        save(root/'activation.json',cli(operator,code));print('Contribution protocol activated by committed publisher transaction.',flush=True)
        return
    if args.command=='contributor':
        contributor=root/'contributor-scifi';contributor.mkdir(mode=0o700,exist_ok=True);os.chown(contributor,1000,1000)
        telemetry=root/'exchange/contributors/cortex-scifi';telemetry.mkdir(parents=True);os.chown(telemetry,1000,1000)
        pin=(root/'exchange/network.sha256').read_text().strip()
        name=operator.container('cortex-scifi')
        run('podman','run','-d','--name',name,'--network','host','--cpus','1','--memory','2g','--pids-limit','128',
            '--cap-drop','ALL','--security-opt','no-new-privileges','-e','DAWL_IMAGE_PIN='+package['images']['contributor'],
            '-v',str(contributor)+':/contributor','-v',str(root/'exchange/bundle')+':/bootstrap:ro',
            '-v',str(telemetry)+':/exchange/status','localhost/dawl-contributor:node-v2','--network-pin',pin,
            '--name','cortex-scifi','--host','127.70.0.22','--deadline',package['deadline'])
        save(root/'contributor-launched.json',dict(at=datetime.now(timezone.utc).isoformat(),name=name,bootstrap='public bundle + two DHT seeds + invited transport; own identity generated inside container'))
        print('Independent newcomer launched.',flush=True)
        return
    if args.command=='publish':
        state=operator.local_cli('status')
        if not state['contributions'] or state['complete']!=state['jobs']:raise ValueError('New corpus is not fully audited')
        result=operator.local_cli('publish');save(root/'contribution-published.json',result);print(json.dumps(result),flush=True)
        return
    if args.command=='report':
        from urllib.request import build_opener,ProxyHandler
        http=build_opener(ProxyHandler({}));evidence=root/'contribution-evidence';evidence.mkdir(exist_ok=True)
        state=operator.local_cli('status');save(evidence/'state.json',state)
        save(evidence/'certificate.json',operator.certificate());save(evidence/'telemetry.json',operator.telemetry())
        records={}
        for node in operator.config['nodes']:
            with http.open('http://'+node['host']+':8903/dht/roles/PROVIDER',timeout=30) as response:records[node['name']]=json.load(response)
        save(evidence/'discovery.json',records)
        for name in ('before-upgrade.json','after-upgrade-before-activation.json','runtime-upgrade.json','activation.json','contributor-launched.json'):
            shutil.copyfile(root/name,evidence/name)
        print(json.dumps(dict(height=state['height'],jobs=state['jobs'],complete=state['complete'],books=len(state['dataset_release']['body']['books']),
            chunks=state['corpus_chunks'],tokens=state['corpus_tokens'],dht_peers_checked=len(records))),flush=True)


if __name__=='__main__':main()
