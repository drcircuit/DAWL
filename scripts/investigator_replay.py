"""Fresh-process offline semantic replay, with no signing keys and no network."""
import argparse
import json
from pathlib import Path
import time

from dawl_evidence.consumer import verify_collection
from dawl_evidence.index import Blueprint, source_bytes
from dawl_evidence.investigator import NliEvaluator, assess_claim, verify_assessment
from dawl_node.common import write_json

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--model',type=Path,required=True);p.add_argument('--model-pin',required=True)
p.add_argument('--dataset',type=Path,required=True);p.add_argument('--blueprint',required=True)
p.add_argument('--corpus',type=Path,required=True);p.add_argument('--examples',type=Path,required=True)
p.add_argument('--investigator',required=True);p.add_argument('--evaluator',required=True)
p.add_argument('--consumer',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():raise ValueError('Preserve previous replay')
bp=Blueprint(a.dataset,a.blueprint);evaluator=NliEvaluator(a.model,a.model_pin)
if evaluator.identity!=a.evaluator:raise ValueError('Evaluator runtime differs')
samples=json.loads((a.examples/'samples.json').read_bytes())
investigations=json.loads((a.examples/'investigations.json').read_bytes())
began=time.monotonic();results=[]
for i,(sample,expected) in enumerate(zip(samples['samples'],investigations['samples']),1):
    collection=json.loads((a.examples/f'example-{i}-collection.json').read_bytes())
    receipt=json.loads((a.examples/f'example-{i}-investigation.json').read_bytes())
    answer=sample['request']['prompt']+sample['response']['text']
    proof=verify_collection(collection,a.consumer,bp,answer,lambda d:source_bytes(a.corpus,d))
    verify_assessment(receipt,a.investigator,collection,a.evaluator,expected['nonce'])
    maximum=0.;exact=True;verdicts=True;pairs=0
    for claim,prior in zip(collection['body']['claims'],receipt['body']['claims']):
        repeated=assess_claim(claim['text'],claim['candidates'],evaluator)
        verdicts=verdicts and repeated['verdict']==prior['verdict']
        for new,old in zip(repeated['judgments'],prior['judgments']):
            pairs+=1;exact=exact and new['raw_nli']==old['raw_nli']
            verdicts=verdicts and new['verdict']==old['verdict']
            ns,os=new['raw_nli']['scores'],old['raw_nli']['scores']
            if (ns is None)!=(os is None):raise ValueError('Context status changed')
            if ns is not None:maximum=max(maximum,max(abs(ns[k]-os[k]) for k in ns))
    results.append({'label':sample['label'],'investigation':receipt['id'],'provenance':proof,
                    'pairs':pairs,'exact_raw_outputs':exact,'verdicts_match':verdicts,
                    'max_absolute_score_difference':maximum})
result={'schema':'hypertrix.investigator-replay/1','evaluator_sha256':a.evaluator,
        'scope':'Fresh CPU process, same hardware/runtime, network disabled, no signing keys; not independent-operator validation.',
        'elapsed_seconds':time.monotonic()-began,'samples':results,
        'accepted':all(r['exact_raw_outputs'] and r['verdicts_match'] for r in results)}
write_json(a.output,result);print(json.dumps(result))
if not result['accepted']:raise SystemExit(1)
