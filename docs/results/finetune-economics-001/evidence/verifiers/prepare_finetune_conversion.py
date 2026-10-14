"""Pair merged weights with the unchanged, originally pinned tokenizer/config.

Transformers 5's tokenizer serialization is not consumed by the pinned converter's
Transformers 4 runtime. Preserve the signed export and construct a separate view.
"""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_node.common import read_json,write_json
from dawl_finetune.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/pilot'))
p.add_argument('--checkpoint',choices=['epoch-1','epoch-2','epoch-3','synthetic-control'],required=True)
a=p.parse_args();view=a.root/'gguf-inputs'/a.checkpoint
record=a.root/f'conversion-inputs-{a.checkpoint}.json'
if view.exists() or record.exists():raise ValueError('Conversion view already exists; preserve it')
base=read_json(a.root/'base-lock.json')['files']
export=next(e for e in read_json(a.root/'exports.json')['exports'] if e['name']==a.checkpoint)
view.mkdir(parents=True);files={}
for name in base:
    source=a.root/('exports/'+a.checkpoint if name=='model.safetensors' else 'base')/name
    expected=export['files'][name] if name=='model.safetensors' else base[name]
    if file_hash(source)!=expected['sha256']:raise ValueError('Conversion source mismatch: '+name)
    os.link(source,view/name)
    files[name]={'source':source.relative_to(a.root).as_posix(),'sha256':file_hash(source),'bytes':source.stat().st_size}
write_json(record,{'schema':'hypertrix.local-conversion-inputs/1','checkpoint':a.checkpoint,'files':files,
                   'reason':'Use original frozen tokenizer/config with merged weights; no vocabulary or architecture change during LoRA adaptation.'})
print(str(view))
