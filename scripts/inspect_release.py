"""Verify and stream every manifest in a certified Blueprint, without training or keys.

Run inside a cluster image with PYTHONPATH=/app/src and a keyless observer directory.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
import httpx
from dawl_node.common import canonical_json,sha256
from dawl_cluster.client import Client
from dawl_cluster.training import TrainingInput
from dawl_platform.artifacts import Objects,PublicPeer
from dawl_swarm.dht import validate


async def inspect(root,seed):
    client=Client(root)
    try:state=await client.state()
    finally:await client.close()
    release=state['dataset_release']
    if not release or release['id']!=sha256(canonical_json(release['body'])):raise ValueError('no valid certified Blueprint')
    body=release['body'];objects=Objects(root/'release-objects')
    sources=[(state['plan']['training_manifest'],client.config['providers'][-1])]
    if body['schema']=='dawl.dataset-release/2':
        async with httpx.AsyncClient(timeout=30,trust_env=False) as http:
            response=await http.get(seed+'/dht/roles/PROVIDER');response.raise_for_status()
        peers=[validate(r,client.config['network_sha256']) for r in response.json()['records']]
        for batch_id in body['contributions']:
            batch=state['contributions'][batch_id]
            peer=next(p for p in peers if p['signer']==batch['provider'] and p['provider']==state['providers'][p['signer']]['endpoint'])
            sources.append((batch['catalog']['training_manifest'],peer['provider']))
        if [d for d,_ in sources]!=body['training_manifests']:raise ValueError('release manifests differ from committed contributions')
    elif body['schema']!='dawl.dataset-release/1':raise ValueError('unsupported Blueprint')
    result=dict(schema='hypertrix.release-input-check/1',dataset_release=release['id'],certified_height=state['height'],
        manifests=len(sources),chunks=0,source_tokens=0,target_tokens=0,splits={},training_executed=False,private_keys_used=False)
    seen=set();book_splits={}
    for descriptor,url in sources:
        peer=PublicPeer(url,True);objects.fetch(peer,descriptor);manifest=objects.read_json(descriptor)
        for row in manifest['records']:
            identity=(row['book_id'],row['source_index'])
            if identity in seen:raise ValueError('duplicate source chunk across manifests')
            seen.add(identity)
            prior=book_splits.setdefault(row['book_id'],row['split'])
            if prior!=row['split']:raise ValueError('book leaks across splits')
        result['chunks']+=manifest['chunks'];result['source_tokens']+=manifest['tokens']
        for item in TrainingInput(manifest,objects,peer).sequences(128):
            bucket=result['splits'].setdefault(item['split'],dict(sequences=0,target_tokens=0,books=set()))
            bucket['sequences']+=1;bucket['target_tokens']+=int(item['loss_mask'].sum());bucket['books'].add(item['book_id'])
    for value in result['splits'].values():
        value['books']=sorted(value['books']);result['target_tokens']+=value['target_tokens']
    if (len(seen)!=result['chunks'] or result['target_tokens']!=result['source_tokens']-result['chunks']
        or result['chunks']!=sum(b['chunks'] for b in body['books']) or result['source_tokens']!=sum(b['tokens'] for b in body['books'])
        or set(book_splits)!={b['book_id'] for b in body['books']}):raise ValueError('released corpus coverage differs from actual training inputs')
    result['books']=len(book_splits)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path('/observer'))
    parser.add_argument('--seed',default='http://127.70.0.21:8903');args=parser.parse_args()
    print(json.dumps(asyncio.run(inspect(args.root,args.seed)),indent=2))
