"""Freeze a reviewed corpus without rewriting acquisition evidence."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
from dawl_node.common import write_json,immutable_write,sha256
from dawl_scratch.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--intake',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
if a.root.exists():raise SystemExit('Reviewed corpus root already exists')
lock=json.loads((a.intake/'source-lock.json').read_bytes());sources=json.loads((a.intake/'sources.json').read_bytes())
if file_hash(a.intake/'sources.json')!=lock['sources_sha256']:raise SystemExit('Intake hash differs')
# Reviewed against the pinned Gutenberg catalog, not inferred from list placement.
fiction={2270,1023,2148,1164,2028,2171,42324,2186,29448}
nonfiction={205}
exclude={2009:'Duplicate edition family: retain Origin of Species ebook 1228',42324:'Duplicate work: retain Frankenstein ebook 84',
         10814:'Spanish reader; outside this English/Norwegian recipe'}
changes=[];documents=[]
for d in sources['documents']:
    d=dict(d)
    if d['id'].startswith('gutenberg:'):
        book=int(d['id'].split(':')[1])
        if book in exclude:
            sources['exclusions'].append({'id':d['id'],'reason':exclude[book]});continue
        domain='books_fiction' if book in fiction else 'books_nonfiction' if book in nonfiction else d['domain']
        if domain!=d['domain']:changes.append({'id':d['id'],'from':d['domain'],'to':domain});d['domain']=domain
    documents.append(d)
plan=json.loads((a.intake/'plan.json').read_bytes())
split_changes=[]
for domain in plan['mixture']:
    for split in ('test','validation'):
        if any(d['domain']==domain and d['split']==split for d in documents):continue
        groups={d['group'] for d in documents if d['domain']==domain and d['split']=='train'}
        if len(groups)<2:raise SystemExit('Insufficient independent source families: '+domain)
        chosen=min(groups,key=lambda g:sha256(('review-holdout-v1/'+domain+'/'+split+'/'+g).encode()))
        for d in documents:
            if d['group']==chosen:
                if d['split']!='train':raise SystemExit('Conflicting source family split')
                d['split']=split
        split_changes.append({'domain':domain,'group':chosen,'from':'train','to':split,'reason':'Ensure held-out coverage after domain corrections; no existing holdout moved into training'})
a.root.mkdir(parents=True);(a.root/'texts').mkdir()
for d in documents:
    source=a.intake/'texts'/d['text_sha256'];target=a.root/'texts'/d['text_sha256']
    if not target.exists():os.link(source,target)
write_json(a.root/'plan.json',plan)
review={'schema':'hypertrix.lab-curation-review/1','intake_lock_sha256':file_hash(a.intake/'source-lock.json'),
        'domain_corrections':changes,'edition_exclusions':exclude,'split_corrections':split_changes,
        'review':'Gutenberg subjects/title review; original attribution and edition metadata retained. Historical works provide language/domain material, not current factual authority.'}
write_json(a.root/'curation-review.json',review)
write_json(a.root/'sources.json',{'documents':documents,'exclusions':sources['exclusions']})
counts=Counter((d['domain'],d['split']) for d in documents)
for domain in plan['mixture']:
    if any(not counts[(domain,s)] for s in ('train','validation','test')):raise SystemExit('Empty reviewed split: '+domain)
new={**lock,'sources_sha256':file_hash(a.root/'sources.json'),'documents':len(documents),'excluded':len(sources['exclusions']),
     'review_sha256':file_hash(a.root/'curation-review.json'),'intake_lock_sha256':review['intake_lock_sha256'],
     'counts':{f'{d}/{s}':n for (d,s),n in sorted(counts.items())},
     'characters':{domain:sum(d['characters'] for d in documents if d['domain']==domain) for domain in plan['mixture']}}
write_json(a.root/'source-lock.json',new);print(json.dumps(new))
