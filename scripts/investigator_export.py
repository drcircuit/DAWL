"""Export only public evaluation records; never copy identity directories or keys."""
import hashlib
import json
from pathlib import Path

repo=Path(__file__).resolve().parents[1]
root=Path('/var/lib/dawl/investigator-001')
out=repo/'docs/results/investigator-001';out.mkdir(parents=True,exist_ok=True)
files={'evaluation.json':root/'evaluation.json','model-manifest.json':root/'model/manifest.json',
       'fixtures.json':repo/'research/investigator-fixtures.json'}
for name in ['trial.json','seed-continuation-investigation.json','positive-retrieval-control-investigation.json',
             'false-retrieval-control-investigation.json']:
    files[name]=root/'live-trial'/name
manifest={}
for name,path in files.items():
    raw=path.read_bytes()
    if b'PRIVATE KEY-----' in raw or b'sk-proj-' in raw:raise ValueError('Private material in export')
    dest=out/name
    if dest.exists() and dest.read_bytes()!=raw:raise ValueError('Public record already differs')
    dest.write_bytes(raw);manifest[name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
sources={}
for path in (root/'runtime-v1/src/dawl_evidence').glob('*.py'):
    sources[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
(out/'evidence.json').write_text(json.dumps({'schema':'hypertrix.investigator-export/1','files':manifest,
       'evidence_implementation':sources},indent=2)+'\n',encoding='utf-8')
print(json.dumps({'exported':len(files),'bytes':sum(f['bytes'] for f in manifest.values())}))
