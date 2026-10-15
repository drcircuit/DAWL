"""Append-only corpus expansion with frozen v1 tokenizer, splits and evaluation windows."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import time
import numpy as np
from tokenizers import Tokenizer
from dawl_node.common import immutable_write, write_json, sha256
from dawl_scratch.data import file_hash, load as old_load, assign_groups
from .progress import progress
from .groups import resolve


def prepare(root, parent, intake):
    root=Path(root)
    if (root/'blueprint.json').exists():raise ValueError('Blueprint exists; use a new dataset root')
    root.mkdir(parents=True,exist_ok=True)
    progress(root,'Checking parent Blueprint',job='Seed II corpus',role='tokenizer',status='running',completed=0,total=1,unit='Blueprint')
    previous,old_arrays=old_load(parent)
    old_sources=json.loads((parent/'sources.json').read_bytes())['documents']
    additions=json.loads((intake/'source-lock.json').read_bytes())['sources']
    groups,group_policy=resolve(additions)
    additions=[{**d,'group':groups[d['id']]} for d in additions if d['id'] in groups]
    write_json(root/'group-policy.json',group_policy)
    splits=assign_groups(d['group'] for d in additions)
    sources=[];seen=set();exclusions=list(group_policy['exclusions'])
    for position,d in enumerate(old_sources):
        raw=(parent/'texts'/d['text_sha256']).read_bytes()
        if sha256(raw)!=d['text_sha256']:raise ValueError('Parent source text changed')
        immutable_write(root/'texts'/d['text_sha256'],raw);seen.add(d['text_sha256']);sources.append(d)
        if position%100==0:progress(root,'Preserving source documents',job='Seed II corpus',role='tokenizer',status='running',
                                    completed=position+1,total=len(old_sources),unit='documents')
    for doc in sorted(additions,key=lambda d:({'test':0,'validation':1,'train':2}[splits[d['group']]],d['id'])):
        d={**doc,'split':splits[doc['group']]}
        if d['text_sha256'] in seen:
            exclusions.append({'id':d['id'],'reason':'exact-duplicate'});continue
        raw=(intake/'texts'/d['text_sha256']).read_bytes()
        if sha256(raw)!=d['text_sha256']:raise ValueError('Intake text changed')
        immutable_write(root/'texts'/d['text_sha256'],raw);seen.add(d['text_sha256']);sources.append(d)
    # Preserve all old rows at their existing indices, so the original 96-window
    # evaluations are exactly comparable, independent of new document order.
    index=json.loads((parent/'windows.json').read_bytes())
    immutable_write(root/'tokenizer.json',(parent/'tokenizer.json').read_bytes())
    tok=Tokenizer.from_file(str(root/'tokenizer.json'));pad=previous['pad_id'];eos=previous['eos_id'];ctx=previous['context']
    new=[d for d in sources if d['id'] not in {o['id'] for o in old_sources}]
    rows=defaultdict(list);tokens=0;began=time.monotonic()
    for i,d in enumerate(new):
        text=(root/'texts'/d['text_sha256']).read_text(encoding='utf-8')
        if any(s in text for s in ('<|bos|>','<|eos|>','<|pad|>')):raise ValueError('Reserved tokenizer token in corpus')
        ids=tok.encode(text,add_special_tokens=False).ids+[eos]
        split=d['split'];offset=len(old_arrays[split]);start=offset+len(rows[split]);targets=0
        for at in range(0,len(ids)-1,ctx):
            chunk=ids[at:at+ctx+1];targets+=len(chunk)-1
            rows[split].append(np.asarray(chunk+[pad]*(ctx+1-len(chunk)),dtype='<u2'))
        index.append({'id':d['id'],'split':split,'domain':d['domain'],'start_row':start,
                      'end_row':offset+len(rows[split]),'target_tokens':targets})
        tokens+=targets
        progress(root,'Tokenizing corpus',job='Seed II corpus',role='tokenizer',status='running',completed=i+1,total=len(new),
                 unit='documents',documents=len(old_sources)+i+1,target_tokens=tokens,
                 targets_per_second=tokens/max(.001,time.monotonic()-began),source=d['origin']['archive_member'])
    descriptors={}
    evaluation={}
    old_index=json.loads((parent/'windows.json').read_bytes())
    for split in ('train','validation','test'):
        values=np.concatenate([old_arrays[split],np.stack(rows[split])])
        np.save(root/(split+'.npy'),values,allow_pickle=False)
        descriptors[split]={'sha256':file_hash(root/(split+'.npy')),'windows':len(values),
                            'targets':sum(i['target_tokens'] for i in index if i['split']==split)}
        if split=='train':continue
        evaluation[split]={}
        for domain in sorted({d['domain'] for d in sources}):
            source=old_index if domain!='norwegian' else index
            indices=[i for row in source if row['split']==split and row['domain']==domain for i in range(row['start_row'],row['end_row'])]
            if indices:evaluation[split][domain]=np.asarray(indices)[np.linspace(0,len(indices)-1,min(32,len(indices)),dtype=int)].tolist()
    write_json(root/'evaluation.json',evaluation)
    write_json(root/'sources.json',{'documents':sources,'exclusions':exclusions,'parent_sources_sha256':previous['sources_sha256'],
                                  'intake_lock_sha256':file_hash(intake/'source-lock.json'),
                                  'catalogue_snapshots_sha256':file_hash(intake/'downloads.json')})
    write_json(root/'windows.json',index)
    blueprint={**previous,'schema':'hypertrix.seed-blueprint/2','parent':file_hash(parent/'blueprint.json'),
        'sources_sha256':file_hash(root/'sources.json'),'windows_sha256':file_hash(root/'windows.json'),
        'evaluation_sha256':file_hash(root/'evaluation.json'),'arrays':descriptors,'documents':len(sources),
        'group_policy_sha256':file_hash(root/'group-policy.json'),
        'domains':dict(Counter(d['domain'] for d in sources)),
        'split_policy':'V1 sources and token rows unchanged; reviewed Norwegian aliases, coauthors and editions form connected source families; unresolved anthologies quarantined; exact deduplication.',
        'tokenizer_fit':'Frozen v1 4096 byte-BPE; no refit. Legacy validation/test windows unchanged. New Norwegian evaluation separate.',
        'limitations':'Historical Norwegian and Danish orthography; near duplicates not exhaustively excluded; source admission is not Oracle certification.'}
    write_json(root/'blueprint.json',blueprint)
    progress(root,'Corpus ready',job='Seed II corpus',role='tokenizer',status='complete',completed=len(new),total=len(new),
             unit='documents',documents=len(sources),target_tokens=descriptors['train']['targets'])
    return blueprint


def load(root):
    blueprint,arrays=old_load(root)
    if file_hash(root/'evaluation.json')!=blueprint['evaluation_sha256']:raise ValueError('Evaluation commitment differs')
    if file_hash(root/'group-policy.json')!=blueprint['group_policy_sha256']:raise ValueError('Group policy differs')
    return blueprint,arrays
