"""A newcomer: public bootstrap only, own keys, DHT join, ingestion and proposal."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0,'/app/src')
import httpx
from dawl_node.common import canonical_json,keygen,load_key,read_json,sha256,sign,write_json
from dawl_cluster.external import check_bundle
from dawl_cluster.client import Client
from dawl_cluster.contribution_artifacts import catalog
from dawl_corpus.corpus import fetch
from dawl_platform.workflow import prepare,tokenize,reconstruct,open_node
from dawl_platform.artifacts import PublicPeer,Objects
from dawl_platform.ledger import Ledger
from dawl_swarm.node import lineage_files
from dawl_swarm.dht import atomic,validate as validate_peer


def bootstrap(root,bundle,pin):
    manifest=check_bundle(bundle,pin)
    if (root/'cluster.json').exists():
        if read_json(root/'cluster.json')['network_sha256']!=pin:raise ValueError('newcomer network changed')
        return
    root.mkdir(parents=True,exist_ok=True)
    if (root/'key.private.pem').exists():raise ValueError('partial newcomer initialization; inspect without key replacement')
    keygen(root/'key.private.pem',root/'public.key')
    for name in ('plan.json','genesis.json','trust.json','lineage.json'):shutil.copyfile(bundle/name,root/name)
    registrations=read_json(bundle/'registrations.json')
    # The bundle stores complete signed registrations, not secret bootstrap keys.
    values=registrations if isinstance(registrations,list) else registrations['registrations']
    members=[r['registration']['body'] for r in values]
    write_json(root/'cluster.json',dict(network_sha256=pin,plan_sha256=manifest['files']['plan.json'],
        genesis_sha256=manifest['files']['genesis.json'],verify_state=True,max_certificate_age_seconds=120,
        rpcs=['http://'+m['host']+':26657' for m in members if m['role']=='validator'],
        providers=['http://'+m['host']+':8900' for m in members if m['role']=='provider']))


async def contribute(root,name,host,selection,deadline,exchange,seeds):
    client=Client(root,load_key(root/'key.private.pem'));children=[];phase='joining';stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def snapshot():
        from container_node import resources
        value=dict(name=name,role='contributor',host=host,site='local',phase=phase,
            capabilities=['PROVIDER','TOKENIZER'],observed_at=datetime.now(timezone.utc).isoformat(),
            image=os.environ.get('DAWL_IMAGE_PIN'),resources=resources(),counters={},events=[],
            public_key=client.public,network_sha256=client.config['network_sha256'])
        if (root/'dht-status.json').exists():value['dht']=read_json(root/'dht-status.json')
        (exchange/'status').mkdir(parents=True,exist_ok=True);atomic(exchange/'status'/(name+'.json'),value)
    async def telemetry():
        while True:snapshot();await asyncio.sleep(3)
    telemetry_task=asyncio.create_task(telemetry())
    try:
        children.append(subprocess.Popen([sys.executable,'/app/scripts/peer_node.py','--root',str(root),'--name',name,'--host',host,
                                         '--roles','PROVIDER,TOKENIZER','--seeds',json.dumps(seeds)]))
        snapshot()
        state=await client.state()
        if not state.get('protocol_upgrade'):raise ValueError('network has not activated contributions')
        if client.public not in state['providers']:
            result=await client.command('announce_provider',dict(name=name,endpoint='http://'+host+':8900',capabilities=['PROVIDER','TOKENIZER']))
            atomic(root/'announcement.json',result)
        phase='ingesting';snapshot()
        # Fetch public shared assets from an existing Cortex; no operator/node-private mount.
        async with httpx.AsyncClient(trust_env=False,timeout=20) as http:
            for _ in range(30):
                try:
                    response=await http.get('http://'+host+':8903/dht/roles/PROVIDER');response.raise_for_status()
                    peers=[validate_peer(r,client.config['network_sha256']) for r in response.json()['records']]
                    available=sorted(p['provider'] for p in peers if p['provider'] in client.config['providers'])
                    if available:break
                except httpx.HTTPError:pass
                await asyncio.sleep(2)
            else:raise RuntimeError('No baseline Cortex found through DHT')
        seed=PublicPeer(available[-1],True);base=root/'public-input';base.mkdir(exist_ok=True)
        trust=read_json(root/'trust.json');base_ledger=Ledger(base/'ledger.sqlite',trust)
        for entry in read_json(root/'lineage.json'):base_ledger.add(entry)
        objects=Objects(base/'objects')
        entries=list(base_ledger.entries())
        table=next(e['body']['payload'] for e in entries if e['body']['kind']=='resource' and e['body']['payload']['type']=='embedding-table')
        tokenizer=next(e['body']['payload'] for e in entries if e['body']['kind']=='resource' and e['body']['payload']['type']=='tokenizer')
        assets=root/'assets';assets.mkdir(exist_ok=True)
        for name_,descriptor in [('embedding.f32le',table['files']['table']),('upstream-model-card.md',table['files']['model_card']),('tokenizer.json',tokenizer['files']['tokenizer'])]:
            path=await asyncio.to_thread(objects.fetch,seed,descriptor);shutil.copyfile(path,assets/name_)
        write_json(assets/'embedding.json',table['descriptor'])
        raw=root/'raw';lock=root/'corpus.lock.json'
        await asyncio.to_thread(fetch,selection,raw,lock)
        ingested=root/'ingested'
        if not ingested.exists():await asyncio.to_thread(prepare,ingested,raw,lock,assets,assets)
        await asyncio.to_thread(tokenize,ingested,1)
        await asyncio.to_thread(reconstruct,ingested,'cpu')
        ledger,published=open_node(ingested);description=await asyncio.to_thread(catalog,ledger,published)
        manifest=sign(dict(schema='hypertrix.contribution-manifest/1',catalog=description,
            trust=read_json(ingested/'trust.json'),lineage=list(ledger.entries())),load_key(root/'key.private.pem'))
        descriptor=published.json(manifest)
        index=lineage_files(ledger);index[descriptor['sha256']]=descriptor
        index[description['training_manifest']['sha256']]=description['training_manifest']
        write_json(ingested/'artifact-index.json',index)
        proposal={'manifest':descriptor,'catalog':description}
        write_json(root/'proposal.json',proposal)
        children.append(subprocess.Popen([sys.executable,'/app/dawl_cluster.py','provider','--root',str(ingested),
                                         '--host',host,'--port','8900','--allow-insecure-lab']))
        async with httpx.AsyncClient(trust_env=False,timeout=3) as http:
            for _ in range(60):
                try:
                    if (await http.get('http://'+host+':8900/health')).status_code==200:break
                except httpx.HTTPError:pass
                await asyncio.sleep(1)
            else:raise RuntimeError('new Cortex did not start')
        phase='announcing-contribution';snapshot()
        identity=sha256(canonical_json({'provider':client.public,**proposal}))
        state=await client.state()
        if identity not in state['contributions']:
            result=await client.command('contribute',proposal);atomic(root/'submitted.json',result)
        phase='running';snapshot()
        print('HYPERTRIX_CONTRIBUTION '+identity,flush=True)
        while not stopping and datetime.now(timezone.utc)<deadline:
            if any(p.poll() is not None for p in children):raise RuntimeError('contributor child exited')
            snapshot();await asyncio.sleep(3)
    finally:
        telemetry_task.cancel();await asyncio.gather(telemetry_task,return_exceptions=True)
        for child in reversed(children):
            if child.poll() is None:child.terminate()
        for child in reversed(children):
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        phase='stopped';snapshot();await client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/contributor'))
    parser.add_argument('--bundle',type=Path,default=Path('/bootstrap'))
    parser.add_argument('--network-pin',required=True);parser.add_argument('--name',required=True);parser.add_argument('--host',required=True)
    parser.add_argument('--selection',type=Path,default=Path('/app/research/corpora/scifi-5-v1.json'))
    parser.add_argument('--deadline',required=True);parser.add_argument('--exchange',type=Path,default=Path('/exchange'))
    parser.add_argument('--seeds',default='["http://127.70.0.20:8903","http://127.70.0.21:8903"]')
    args=parser.parse_args();bootstrap(args.root,args.bundle,args.network_pin)
    asyncio.run(contribute(args.root,args.name,args.host,args.selection,datetime.fromisoformat(args.deadline),args.exchange,json.loads(args.seeds)))
