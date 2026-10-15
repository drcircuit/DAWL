"""Build a source kit beside a separately exported, pinned OCI runtime."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
p.add_argument('--archive',type=Path,required=True);p.add_argument('--image-id',required=True);p.add_argument('--name',default='hypertrix-home-lab-20261015')
p.add_argument('--frozen-src',type=Path,action='append',default=[]);a=p.parse_args()
def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
base=a.output/a.name
if base.exists():raise SystemExit('Release directory exists; choose a new name')
base.mkdir(parents=True)
files=[p for p in (a.repo/'src').rglob('*.py') if '__pycache__' not in p.parts]
files += [p for p in (a.repo/'lab').iterdir() if p.suffix in ('.py','.sh','.ps1','.md','.html')]
files += [a.repo/name for name in ('Dockerfile.lab','research/corpora/seed-lab-v1.json','research/corpora/seed-lab-quality-prompts.json',
                                  'scripts/lab_benchmark.py','scripts/lab_quality.py','scripts/lab_report.py',
                                  'scripts/lab_review_corpus.py','scripts/lab_integration.py','docs/home_training_lab.md')]
files += list((a.repo/'tests').glob('test_lab_*.py'))
entries=[];runtime_index={}
for source in sorted(files):
    raw=source.read_bytes()
    if re.search(rb'sk-proj-[A-Za-z0-9_-]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',raw):raise ValueError('Sensitive-looking content: '+source.name)
    name=source.relative_to(a.repo).as_posix();target=base/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    entries.append({'path':name,'sha256':digest(target),'bytes':len(raw)})
def pins(source):
    paths=list((source/'dawl_lab').glob('*.py'))+[source/name for name in ('dawl_node/common.py','data_ingestion/protocol.py','dawl_scratch/data.py','dawl_finetune/data.py','dawl_seed/progress.py')]
    values={p.relative_to(source).as_posix():digest(p) for p in paths}
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
runtime_index[pins(a.repo/'src')]='src'
for source in a.frozen_src:
    identity=pins(source)
    if identity in runtime_index:continue
    location='lab/training-runtimes/'+identity+'/src';runtime_index[identity]=location
    for path in source.rglob('*.py'):
        if '__pycache__' in path.parts:continue
        name=location+'/'+path.relative_to(source).as_posix();target=base/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        entries.append({'path':name,'sha256':digest(target),'bytes':target.stat().st_size})
index=base/'lab/runtime-index.json';index.write_text(json.dumps(runtime_index,sort_keys=True))
entries.append({'path':'lab/runtime-index.json','sha256':digest(index),'bytes':index.stat().st_size})
manifest={'schema':'hypertrix.lab-release/1','created_at':datetime.now(timezone.utc).isoformat(),'name':a.name,'files':entries,
          'runtime':{'image_id':a.image_id,'archive':a.archive.name,'archive_sha256':digest(a.archive),'archive_bytes':a.archive.stat().st_size},
          'scope':'Code and pinned runtime. Node identities are created on each machine; corpus and checkpoints travel as signed packages.'}
(base/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
archive=a.output/(a.name+'.zip')
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(base.rglob('*')):
        if path.is_file():z.write(path,path.relative_to(base))
receipt={'kit':archive.name,'kit_sha256':digest(archive),'kit_bytes':archive.stat().st_size,
         'manifest_sha256':digest(base/'release-manifest.json'),'runtime':manifest['runtime'],'files':len(entries)}
(a.output/(a.name+'.release.json')).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
