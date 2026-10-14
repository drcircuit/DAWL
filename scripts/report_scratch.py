"""Export compact metadata for the scratch experiment, never source text or private keys."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def report(root,output):
    output.mkdir(parents=True,exist_ok=True)
    dataset=root/'dataset'
    blueprint=json.loads((dataset/'blueprint.json').read_bytes())
    (output/'blueprint.json').write_bytes((dataset/'blueprint.json').read_bytes())
    sources=json.loads((dataset/'sources.json').read_bytes())
    counts=Counter((d['split'],d['domain']) for d in sources['documents'])
    result={'schema':'hypertrix.seed-report/1','blueprint':blueprint,'blueprint_sha256':digest(dataset/'blueprint.json'),
        'source_counts':{'/'.join(k):v for k,v in counts.items()},'duplicates_removed':len(sources['exclusions']),
        'models':{},'scope':'Local training and same-device replay; no Oracle certification or independent operator claim.'}
    receipt=root/'data-verifier/receipt.json'
    if receipt.exists():
        result['data_verification']=json.loads(receipt.read_bytes())
        (output/'data-verification.json').write_bytes(receipt.read_bytes())
        (output/'data-verifier.public.key').write_bytes((root/'data-verifier/public.key').read_bytes())
        (output/'data-verifier-implementation.py').write_bytes((root/'data-verifier/implementation.py').read_bytes())
    if (root/'runtime-image-id.txt').exists():
        result['runtime_image_id']=(root/'runtime-image-id.txt').read_text().strip()
    for name in ('dense','moe'):
        model=root/name
        if not (model/'summary.json').exists():continue
        recipe=json.loads((model/'recipe.json').read_bytes())
        result['models'][name]={'summary':json.loads((model/'summary.json').read_bytes()),
            'baseline':json.loads((model/'baseline.json').read_bytes()),'recipe':recipe,
            'operator_public_key':(model/'operator.public.key').read_text().strip(),
            'lineage_sha256':digest(model/'lineage.jsonl'),
            'replay':[{k:v for k,v in json.loads(p.read_bytes()).items() if k!='checks'} for p in sorted(model.glob('replay-*.json'))]}
        for file in ('lineage.jsonl','recipe.json','config.json','implementation.json','operator.public.key','baseline.json','summary.json'):
            (output/(name+'-'+file)).write_bytes((model/file).read_bytes())
        descriptors=output/(name+'-checkpoints')
        descriptors.mkdir(exist_ok=True)
        for p in sorted((model/'checkpoints').glob('*/checkpoint.json')):
            (descriptors/(p.parent.name+'.json')).write_bytes(p.read_bytes())
        for p in model.glob('replay-*.json'):(output/(name+'-'+p.name)).write_bytes(p.read_bytes())
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=report(a.root,a.output);print(json.dumps({'models':list(r['models']),'documents':r['blueprint']['documents']}))
