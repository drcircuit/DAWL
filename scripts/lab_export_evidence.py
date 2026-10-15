"""Export an explicit public evidence allowlist, never data or private identities."""
import argparse
import hashlib
import json
from pathlib import Path
import re

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--release',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
names=['dataset/blueprint.json','data-verification.json','benchmark/benchmark.json','compile-benchmark-v2/benchmark.json',
       'compile-batch32/benchmark.json','probe/artifact-verification.json','probe/replay-0000010-0000020.json',
       'probe/lineage.jsonl','probe/recipe.json','probe/implementation.json','probe/operator.public.key',
       'integration-004/integration.json','isolated-auditor/result/receipt.json','import-check/doctor.json',
       'calibration-publication.json','calibration-transfer.json','calibration-package-verification.json',
       'run/recipe.json','run/schedule.json']
entries=[]
for name in names:
    source=a.root/name;raw=source.read_bytes()
    if len(raw)>2_000_000:raise ValueError('Unexpected evidence size: '+name)
    if re.search(rb'sk-proj-[A-Za-z0-9_-]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',raw):raise ValueError('Sensitive content rejected')
    target=a.output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    entries.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
release=a.release.read_bytes();(a.output/'release.json').write_bytes(release)
entries.append({'path':'release.json','sha256':hashlib.sha256(release).hexdigest(),'bytes':len(release)})
manifest={'schema':'hypertrix.lab-evidence-export/1','status':'calibration-complete-main-training-active',
          'files':entries,'scope':'Frozen evidence export before completion of the 6000-update phase. No final language-quality or multi-machine GPU result is claimed.'}
(a.output/'evidence.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'files':len(entries),'bytes':sum(row['bytes'] for row in entries),'output':str(a.output)}))
