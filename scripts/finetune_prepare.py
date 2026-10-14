"""Prepare hash-locked local weights and 13 existing plus 3 economics books."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
sys.path.insert(0, '/repo/src'); sys.path.insert(0, '/repo/scripts')
from dawl_node.common import write_json, read_json, sha256
from profile_corpus_pilot import corpus


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def prepare(root, repo):
    if (root/'source-lock.json').exists(): raise ValueError('prepared experiment exists; use a new root')
    base=root/'base'; base.mkdir(exist_ok=True)
    lock=read_json(repo/'artifacts/tokenizer-candidates/qwen3/download-lock.json')
    weights=read_json(repo/'artifacts/profile-weights/qwen3/download-lock.json')
    if (weights['repo'],weights['revision'])!=(lock['repo'],lock['revision']): raise ValueError('base revision mismatch')
    source=repo/'artifacts/profile-weights/qwen3/model.safetensors'
    if digest(source)!=weights['sha256'] or source.stat().st_size!=weights['bytes']: raise ValueError('base checkpoint mismatch')
    shutil.copyfile(source,base/'model.safetensors')
    files={'model.safetensors':{'sha256':weights['sha256'],'bytes':weights['bytes']}}
    for name,desc in lock['files'].items():
        source=repo/'artifacts/tokenizer-candidates/qwen3'/name
        if digest(source)!=desc['sha256']: raise ValueError('tokenizer/config mismatch')
        shutil.copyfile(source,base/name); files[name]=desc
    for name in ('generation_config.json','vocab.json','merges.txt','LICENSE'):
        url=f"https://huggingface.co/{lock['repo']}/resolve/{lock['revision']}/{name}"
        with urllib.request.urlopen(url,timeout=90) as response: payload=response.read(32*1024*1024)
        (base/name).write_bytes(payload); files[name]={'sha256':sha256(payload),'bytes':len(payload),'url':url}
    write_json(root/'base-lock.json',{'repo':lock['repo'],'revision':lock['revision'],'license':'Apache-2.0','files':files})
    books=[]; raw_dir=root/'raw'; raw_dir.mkdir(exist_ok=True)
    for book,raw in corpus():
        (raw_dir/f"pg{book['id']}.txt").write_bytes(raw)
        books.append({**book,'domain':'literature','file':f"raw/pg{book['id']}.txt"})
    for book in read_json(repo/'research/corpora/economics-3-v1.json')['books']:
        path=raw_dir/f"pg{book['id']}.txt"; rdf=raw_dir/f"pg{book['id']}.rdf"
        raw=path.read_bytes(); raw.decode('utf-8')
        tree=ET.fromstring(rdf.read_bytes()); ns={'dcterms':'http://purl.org/dc/terms/'}
        rights=' '.join(tree.find('.//dcterms:rights',ns).itertext()).strip()
        if rights!='Public domain in the USA.': raise ValueError('unapproved rights declaration')
        books.append({**book,'domain':'economics','file':f"raw/pg{book['id']}.txt",'sha256':sha256(raw),'bytes':len(raw),
                      'metadata_sha256':digest(rdf),'rights_declaration':rights,
                      'source_url':f"https://www.gutenberg.org/ebooks/{book['id']}",
                      'download_uri':f"rsync://gutenberg.pglaf.org/gutenberg-epub/{book['id']}/pg{book['id']}.txt"})
    write_json(root/'source-lock.json',{'schema':'hypertrix.finetune-sources/1','books':sorted(books,key=lambda b:b['id']),
               'acquired_at':datetime.now(timezone.utc).isoformat(),'scope':'Raw originals preserved; cleaned derivatives and training splits are separately hashed.'})
    print(json.dumps({'base':lock['repo'],'revision':lock['revision'],'books':len(books),'raw_bytes':sum(b['bytes'] for b in books)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/pilot'));p.add_argument('--repo',type=Path,default=Path('/repo'))
    a=p.parse_args();prepare(a.root,a.repo)
