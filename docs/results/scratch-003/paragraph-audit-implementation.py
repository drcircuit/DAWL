"""Bounded exact-paragraph leakage check, separate from broader near-duplicate research."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_scratch.data import file_hash


def audit(dataset):
    bp=json.loads((dataset/'blueprint.json').read_bytes())
    if file_hash(dataset/'sources.json')!=bp['sources_sha256']:raise ValueError('Source manifest differs')
    docs=json.loads((dataset/'sources.json').read_bytes())['documents'];paragraphs=defaultdict(list);sizes={};splits={}
    for d in docs:
        if d['domain']!='norwegian':continue
        path=dataset/'texts'/d['text_sha256']
        if file_hash(path)!=d['text_sha256']:raise ValueError('Text bytes differ')
        text=path.read_text(encoding='utf-8');sizes[d['id']]=len(text);splits[d['id']]=d['split']
        for part in set(p.strip() for p in text.splitlines() if len(p.strip())>=200):
            paragraphs[hashlib.sha256(part.encode()).hexdigest()].append((d['id'],len(part)))
    overlap=Counter();pairs=Counter()
    for values in paragraphs.values():
        if len({splits[id] for id,n in values})>1:
            for id,n in values:overlap[id]+=n
            for i,(a,n) in enumerate(values):
                for b,_ in values[i+1:]:
                    if splits[a]!=splits[b]:pairs[tuple(sorted((a,b)))]+=n
    return {'schema':'hypertrix.paragraph-overlap-audit/1','blueprint_sha256':file_hash(dataset/'blueprint.json'),
        'implementation_sha256':file_hash(Path(__file__)),'documents':len(sizes),'unique_paragraphs':len(paragraphs),
        'minimum_characters':200,'cross_split_document_pairs':len(pairs),
        'highest_document_overlap':[{'id':id,'split':splits[id],'fraction':n/sizes[id],'characters':n} for id,n in overlap.most_common(12)],
        'highest_pairs':[{'ids':list(pair),'characters':n} for pair,n in pairs.most_common(12)],
        'scope':'Exact stripped paragraph overlap of at least 200 Unicode characters in the Norwegian domain; not exhaustive near-duplicate detection.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.dataset);a.output.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
