"""Offline signature, data allocation and weight-history checks (no tensor replay)."""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import read_json,verify_signature,canonical_json,sha256
from dawl_profiles.profiles import describe
from dawl_training.protocol import validate_stage,schedule,validate_update,round_body,certify


def verify(root):
    stage=read_json(root/'stage.json'); b=validate_stage(stage)
    dataset=read_json(root/'dataset.json')
    if describe(canonical_json(dataset))!=b['dataset']: raise ValueError('exported dataset differs from stage')
    split_books=[{r['book'] for r in dataset[s]} for s in ('train','validation','test')]
    if any(a&c for i,a in enumerate(split_books) for c in split_books[i+1:]): raise ValueError('books leak across splits')
    chain=read_json(root/'chain.json'); updates=read_json(root/'updates.json')
    if len(chain)!=b['recipe']['rounds'] or len(updates)!=len(chain): raise ValueError('incomplete training history')
    parent=stage['id']; tokens=0; selected=set(); previous=b['initial_adapter']; changed=0
    for r,record in enumerate(chain):
        if len(updates[r])!=len(b['participants']): raise ValueError('missing training contribution')
        for p,update in enumerate(updates[r]):
            claim=validate_update(update,stage,dataset,r,p,parent); tokens+=claim['allocation']['target_tokens']
            for window in schedule(dataset,stage,r,p):
                identity=sha256(canonical_json(window))
                if identity in selected: raise ValueError('training window allocated twice')
                selected.add(identity)
        body=round_body(stage,r,parent,updates[r],record['body']['adapter'])
        if certify(body,record['acceptances'],stage)!=record: raise ValueError('invalid checkpoint acceptance')
        changed+=body['adapter']!=previous; previous=body['adapter']; parent=record['id']
    completions=read_json(root/'completions.json')
    if len(completions)!=len(b['participants']): raise ValueError('missing completion')
    for p,completion in enumerate(completions):
        c=verify_signature(completion,b['participants'][p]['public'])
        if (c['stage'],c['checkpoint'],c['adapter'],c['rounds'])!=(stage['id'],parent,previous,len(chain)):
            raise ValueError('wrong completion head')
    audit=read_json(root/'audit.json'); checked=verify_signature(audit,b['auditor'])
    if (checked['stage']!=stage['id'] or checked['checkpoint']!=parent or checked['rounds_verified']!=len(chain)
            or not checked['replicas_identical'] or not checked['aggregation_recomputed']): raise ValueError('wrong audit scope')
    challenge=checked['challenge']
    expected=sorted([(r,p) for r in range(len(chain)) for p in range(2)],
        key=lambda x:sha256(canonical_json({'nonce':challenge['nonce'],'head':parent,'round':x[0],'peer':x[1]})))[:2]
    if challenge['committed_head']!=parent or challenge['samples']!=[list(x) for x in expected]:
        raise ValueError('challenge selection differs')
    if len(checked['numerical_training_replays'])!=2: raise ValueError('missing sampled replay')
    for check,(r,p) in zip(checked['numerical_training_replays'],expected):
        if check!={'round':r,'peer':p,'bit_exact':True,'adapter':updates[r][p]['body']['adapter']}:
            raise ValueError('audit replay has wrong target')
    release=read_json(root/'release.json'); body=release['body']
    if (body['schema']!='hypertrix.training-release/1' or body['stage']!=stage['id'] or body['checkpoint']!=parent
            or body['audit']!=audit['id'] or body['parent_model']!=b['parent_model']['sha256']
            or body['profile_id']!=b['profile']['id'] or body['fixed_projection']!=b['fixed_projection']
            or body['adapter']!=previous or certify(body,release['acceptances'],stage)!=release):
        raise ValueError('invalid audited model release')
    return {'verified':True,'stage':stage['id'],'release':release['id'],'rounds':len(chain),'signed_contributions':len(chain)*2,
        'target_tokens':tokens,'unique_training_windows':len(selected),'changed_checkpoints':changed,
        'numerically_replayed_contributions':2,
        'scope':'Offline signature, allocation and history checks. Trust pins must be independently obtained. Numerical replay, source re-tokenization and historical quorum verification ran in the separate auditor.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,default=ROOT/'docs/results/training-run-001')
    print(canonical_json(verify(p.parse_args().root)).decode())
