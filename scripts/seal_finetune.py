"""Bind the actual served artifacts and frozen answers to the local signed trail.

Run only after trainer, exporter and replay have stopped writing lineage.
This adds an operator attestation; it does not create an Oracle certificate.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from dawl_node.common import canonical_json, load_key, read_json, sign, write_json
from dawl_finetune.data import file_hash


def seal(root, responses, cases):
    if (root/'serving-package.json').exists():raise ValueError('Serving package already sealed')
    summary=read_json(root/'training-summary.json')
    replay=read_json(root/'replay-verification.json')
    read_json(root/'exports.json')
    selected=summary['selected_evaluation']['selected']
    models=read_json(root/'ollama-models.json')['models']
    served={m['id']:m for m in models if m['id'] in ('open','hypertrix')}
    if set(served)!=set(('open','hypertrix')):raise ValueError('Both local comparison models must be registered')
    if served['hypertrix']['checkpoint']!=selected:raise ValueError('Serving selection differs from frozen evaluation')
    files={}
    def include(path):
        relative=path.relative_to(root).as_posix()
        files[relative]={'sha256':file_hash(path),'bytes':path.stat().st_size}
    for name in ('source-lock.json','base-lock.json','dataset-lock.json','segments.jsonl','recipe.json',
                 'probes-lock.json','gpu-session.json','baseline.json','selected-evaluation.json',
                 'training-summary.json','control-result.json','control-codebook.json','exports.json',
                 'runtime-image-002-id.txt','verifier-replay.py','replay-verification.json'):
        include(root/name)
    for name in ('merge-verification.json','merge-verification-single-round.json','merge-checker-single-round.py'):
        include(root/name)
    for pattern in ('replay-checkpoint-*.json','replay-init-*.json','replay-*-diagnostic.json','verifier-initialization.py','verifier-kernels.py'):
        for path in root.glob(pattern):include(path)
    for model in served.values():
        include(root/('ollama-'+model['checkpoint']+'.json'))
        path=root/(model['checkpoint']+'-bf16.gguf')
        if file_hash(path)!=model['gguf_sha256']:raise ValueError('Served GGUF changed')
        include(path)
        conversion=root/('conversion-inputs-'+model['checkpoint']+'.json')
        if conversion.exists():include(conversion)
    case_ids=[c['id'] for c in read_json(cases)['cases']]
    expected={f'{m}--{c}.json' for m in ('open','openai','hypertrix') for c in case_ids}
    actual={p.name for p in responses.glob('*.json')}
    if actual!=expected:raise ValueError('Expected one frozen answer per model and case')
    copied=[]
    for source in [cases]+sorted(responses.glob('*.json')):
        destination=root/'serving-evidence'/source.name
        destination.parent.mkdir(exist_ok=True)
        with destination.open('xb') as f:f.write(source.read_bytes())
        include(destination);copied.append(destination.relative_to(root).as_posix())
    for name in ('trust_lab.html','trust_lab.py','trust_provenance.py','trust_provenance.js','trust_provenance.css'):
        destination=root/'lab-interface'/name;destination.parent.mkdir(exist_ok=True)
        with destination.open('xb') as f:f.write((REPO/'scripts'/name).read_bytes())
        include(destination)
    for name in ('verify_finetune.py','verify_finetune_merge.py','prepare_finetune_conversion.py','record_finetune_replay_outcome.py'):
        destination=root/'verifiers'/name;destination.parent.mkdir(exist_ok=True)
        with destination.open('xb') as f:f.write((REPO/'scripts'/name).read_bytes())
        include(destination)
    for path in (root/'execution-notes').glob('*'):
        if path.is_file():include(path)
    package={'schema':'hypertrix.local-serving-package/1','sealed_utc':datetime.now(timezone.utc).isoformat(),
             'selected_checkpoint':selected,'local_models':list(served.values()),'files':files,
             'replay_status':replay['status'],'converter_commit':'6868a52cb337450658006ad2f9281c2eb63f1666',
             'conversion':'PEFT 0.21.2 safe CPU merge: BF16(BF16(base) + BF16((alpha/r) * FP32(B) @ FP32(A))), then pinned llama.cpp BF16 GGUF export with the original tokenizer/config. This is a derived serving artifact.',
             'scope':'Local operator attestation connecting training, verification and serving artifacts; no Oracle quorum or independently operated validator.'}
    write_json(root/'serving-package.json',package)
    path=root/'lineage.jsonl';rows=[json.loads(l) for l in path.read_text().splitlines() if l]
    record=sign({'schema':'hypertrix.local-experiment-record/1','sequence':len(rows),'previous':rows[-1]['id'],
                 'kind':'serving-package','payload':{'file':'serving-package.json','sha256':file_hash(root/'serving-package.json')}},
                load_key(root/'operator.private.pem'))
    with path.open('a') as f:f.write(canonical_json(record).decode()+'\n');f.flush()
    return {'lineage_id':record['id'],'bound_files':len(files),'frozen_answers':len(expected),'selected':selected}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--responses',type=Path,required=True);p.add_argument('--cases',type=Path,required=True)
    a=p.parse_args();print(json.dumps(seal(a.root,a.responses,a.cases),indent=2))
