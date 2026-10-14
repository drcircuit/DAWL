"""Import one exact BF16 GGUF into local Ollama and record its identity."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import read_json,canonical_json
from dawl_finetune.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--pilot',type=Path,required=True);p.add_argument('--checkpoint',choices=['base','epoch-1','epoch-2','epoch-3','synthetic-control'],required=True)
p.add_argument('--selected',action='store_true');a=p.parse_args()
name='hypertrix-qwen3-base:001' if a.checkpoint=='base' else 'hypertrix-economics-'+a.checkpoint+':001'
source=a.pilot/(a.checkpoint+'-bf16.gguf')
if not source.is_file():raise ValueError('Convert the exact checkpoint first')
folder=ROOT/'artifacts/trust-lab-001';folder.mkdir(parents=True,exist_ok=True)
modelfile=folder/('Modelfile.'+a.checkpoint+'.gguf')
modelfile.write_text('FROM "'+str(source).replace('\\','/')+'"\nPARAMETER num_ctx 4096\nPARAMETER temperature 0.7\nPARAMETER top_p 0.8\nPARAMETER top_k 20\n',encoding='utf-8')
log=folder/('import-'+a.checkpoint+'.log')
with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=20) as response:models=json.load(response)['models']
digest=file_hash(source)
if any(m['name']==name for m in models):
    request=urllib.request.Request('http://127.0.0.1:11434/api/show',data=json.dumps({'model':name}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=20) as response:existing=json.load(response)
    if digest not in existing['modelfile']:raise ValueError('Existing model name points to different GGUF bytes')
else:
    with log.open('w',encoding='utf-8') as f:subprocess.run(['ollama','create',name,'-f',str(modelfile)],stdout=f,stderr=f,check=True)
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=20) as response:models=json.load(response)['models']
model=next(m for m in models if m['name']==name)
desc={'id':'open' if a.checkpoint=='base' else 'hypertrix' if a.selected else a.checkpoint,
      'name':name,'label':'Qwen3-0.6B · unchanged baseline' if a.checkpoint=='base' else 'Hypertrix · '+a.checkpoint,
      'provider':'ollama','checkpoint':a.checkpoint,'digest':model['digest'],'gguf_sha256':digest,
      'gguf_bytes':source.stat().st_size,'format':'BF16 GGUF','converter_commit':'6868a52cb337450658006ad2f9281c2eb63f1666'}
record=a.pilot/('ollama-'+a.checkpoint+'.json');payload=canonical_json(desc)
if record.exists():
    if record.read_bytes()!=payload:raise ValueError('Serving descriptor already exists with different contents')
else:
    # WSL's Windows UNC bridge does not implement hard links. Exclusive creation
    # still refuses replacement; this small descriptor is written only once.
    with record.open('xb') as f:f.write(payload)
path=a.pilot/'ollama-models.json';registry=read_json(path) if path.exists() else {'models':[]}
registry['models']=[m for m in registry['models'] if m['id']!=desc['id']]+[desc]
pending=path.with_suffix('.pending');pending.write_text(json.dumps(registry,indent=2));pending.replace(path)
print(json.dumps(desc,indent=2))
