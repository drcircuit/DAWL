"""Investigate the saved, unchanged Seed examples against the current shared corpus."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
import secrets

import httpx

from dawl_evidence.consumer import collect, discover as discover_sources
from dawl_evidence.assessment_service import discover, request_assessment
from dawl_lab.mesh import configure_policy
from dawl_evidence.index import Blueprint
from dawl_node.common import canonical_json, load_key, sha256, write_json

repo=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--examples',type=Path,default=repo/'docs/results/seed-architecture-samples-001')
p.add_argument('--sentences',action='store_true')
a=p.parse_args();out=a.examples
if (out/'investigations.json').exists():raise ValueError('Preserve the recorded investigation')
samples=json.loads((out/'samples.json').read_bytes())
root=Path('/var/lib/dawl/investigator-001');evidence=Path('/var/lib/dawl/evidence-001')
network_policy=json.loads((evidence/'network.json').read_bytes())
network=sha256(canonical_json(network_policy));configure_policy(network_policy,network)
bp=Blueprint(Path('/var/lib/dawl/seed-lab-001/dataset'),'4b7331add9246e4c966cbb67478687755f9daf5bfe5c19124effcb41affb7c63')
evaluator=json.loads((root/'evaluation.json').read_bytes())['evaluator_sha256']
public=(root/'peer/key.public.hex').read_text().strip()
provider=(evidence/'provider-peer/key.public.hex').read_text().strip()
key=load_key(evidence/'consumer-peer/key.private.pem')
result={'schema':'hypertrix.seed-example-investigations/1','created_at':datetime.now(timezone.utc).isoformat(),
        'blueprint':bp.identity,'corpus':'Seed III training corpus: 145,011 documents; 597,868 passages',
        'scope':'Same reference corpus for all models; Blueprint membership does not establish prior-model training exposure or causal influence.',
        'claim_selection':('Deterministic sentence boundaries; up to 8 consecutive exact spans of prompt plus continuation, trailing fragment retained, no rewrites.' if a.sentences else 'Exact prompt plus continuation as one span, including incomplete endings; no rewrites or atomic claim decomposition.'),
        'evaluator_sha256':evaluator,'investigator':public,'samples':[]}
with httpx.Client(timeout=180,follow_redirects=False,trust_env=False) as client:
    providers=discover_sources(client,'http://127.80.0.16:8913',network,bp.identity,{provider})
    peers=discover(client,'http://127.80.0.16:8913',network,bp.identity,{public},evaluator)
    if not providers or len(peers)!=1:raise ValueError('Required admitted roles are unavailable')
    for i,sample in enumerate(samples['samples']):
        answer=sample['request']['prompt']+sample['response']['text']
        checkpoint=sample['response'].get('checkpoint_sha256',sample['response'].get('checkpoint'))
        spans=[];start=0
        if a.sentences:
            for match in re.finditer(r'(?<=[.!?])\s+(?=[A-ZÆØÅ“"\n])',answer):
                end=match.start()
                if end>start and len(spans)<7:spans.append({'start':start,'end':end});start=match.end()
        spans.append({'start':start,'end':len(answer)})
        collection=collect(answer,spans,checkpoint,bp,providers,key,client)
        nonce=secrets.token_hex(32)
        receipt=request_assessment(client,peers[0],collection,answer,evaluator,key,nonce)
        write_json(out/f'example-{i+1}-collection.json',collection)
        write_json(out/f'example-{i+1}-investigation.json',receipt)
        result['samples'].append({'label':sample['label'],'answer_sha256':sha256(answer.encode()),
            'collection':collection['id'],'investigation':receipt['id'],'nonce':nonce,
            'claims':[{'result':r,'candidates':c['candidates']} for r,c in zip(receipt['body']['claims'],collection['body']['claims'])],
            'result':receipt['body']['claims'][0],'candidates':collection['body']['claims'][0]['candidates'],
            'elapsed_seconds':receipt['body']['elapsed_seconds'],'retrieval_failures':collection['body']['failures']})
        print(json.dumps({'model':sample['label'],'results':[r['verdict'] for r in receipt['body']['claims']],
                          'sources':sum(len(c['candidates']) for c in collection['body']['claims'])}),flush=True)
write_json(out/'investigations.json',result)
