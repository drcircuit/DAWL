"""Prepare a fresh admitted training stage from a certified token Blueprint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src')); sys.path.insert(0,str(ROOT/'scripts'))
from dawl_node.common import keygen, read_json, write_json
from dawl_profiles.profiles import content_id
from dawl_profiles.workspace import Workspace
from dawl_training.data import make_dataset
from dawl_training.protocol import validate_stage,schedule,implementation
from profile_gpu_pilot import certificate,lineage


def prepare(root,repo,source):
    root.mkdir(parents=True,exist_ok=False)
    public=root/'provider'; ws=Workspace(public); evidence=public/'evidence'; evidence.mkdir()
    for name in ('genesis.json','completed-certificate.json','public-evidence.json'):
        shutil.copyfile(repo/'docs/results/profile-testnet-002'/name,evidence/name)
    for path in (source/'objects').iterdir():
        if len(path.name)==64:
            try: os.link(path,ws.objects.root/path.name)
            except OSError: shutil.copyfile(path,ws.objects.root/path.name)
    state,cert=certificate(evidence)
    profile,head,entries,replay=lineage(ws,state,'smollm2',ws.objects.read)
    dataset=make_dataset(ws.objects,entries)
    lock=read_json(repo/'artifacts/profile-weights/smollm2/download-lock.json')
    tokenizer_lock=read_json(repo/'artifacts/tokenizer-candidates/smollm2/download-lock.json')
    if (lock['repo'],lock['revision'])!=(tokenizer_lock['repo'],tokenizer_lock['revision']): raise ValueError('weight/profile revisions differ')
    if tokenizer_lock['files']['config.json']['sha256']!=profile['body']['model']['config']['sha256']:
        raise ValueError('config acquisition differs from certified profile')
    path=repo/'artifacts/profile-weights/smollm2'/lock['filename']
    digest=hashlib.sha256(); chunks=[]
    with path.open('rb') as stream:
        while data:=stream.read(8*1024**2): digest.update(data); chunks.append(ws.objects.put(data))
    if path.stat().st_size!=lock['bytes'] or digest.hexdigest()!=lock['sha256']: raise ValueError('upstream checkpoint mismatch')
    from dawl_training.model import load_model,export
    model=load_model(ws.objects.read(profile['body']['model']['config']),path)
    adapter_layout,adapter=export(model); projection_layout,projection=export(model,'A')
    peers=[]
    for i in range(2):
        node=root/f'peer-{i}'
        public_key=keygen(node/'key.private.pem',node/'key.public.hex')
        peers.append({'id':i,'public':public_key,'url':f'http://127.70.0.{31+i}:8910'})
    auditor=keygen(root/'auditor/key.private.pem',root/'auditor/key.public.hex')
    body={'schema':'hypertrix.training-stage/1','implementation_sha256':implementation(),'profile':profile,'token_blueprint':head['id'],
        'source_certificate':cert,'participants':peers,'auditor':auditor,'auditor_url':'http://127.70.0.33:8910',
        'upstream':lock,'parent_model':{'sha256':lock['sha256'],'bytes':lock['bytes'],'chunks':chunks},
        'dataset':ws.objects.json(dataset),'adapter_layout':adapter_layout,'initial_adapter':ws.objects.put(adapter),
        'projection_layout':projection_layout,'fixed_projection':ws.objects.put(projection),
        'recipe':{'algorithm':'fixed-A-LoRA/local-SGD/unanimous-token-weighted-mean/v1',
            'optimizer':'SGD/no-momentum/no-weight-decay','dtype':'float32','seed':20261014,
            'rank':8,'alpha':16,'targets':['q_proj','v_proj'],'sequence_length':128,'batch_size':2,
            'rounds':32,'local_steps':4,'learning_rate':0.1,'clip_grad_norm':1.0,'max_update_l2':2.0},
        'policy':'Fixed admitted roster; every peer accepts every checkpoint. Local branch, not Oracle promotion.',
        'runtime':{'torch':'2.11.0+cu128','transformers':'5.19.0','tokenizers':'0.23.3'},
        'provider':'http://127.70.0.30:8900'}
    stage={'id':content_id(body),'body':body}; validate_stage(stage)
    selected=[record for r in range(body['recipe']['rounds']) for i in range(2) for record in schedule(dataset,stage,r,i)]
    if len({content_id(r) for r in selected})!=len(selected): raise ValueError('overlapping training allocation')
    for name in ('peer-0','peer-1','auditor'):
        bootstrap=root/name/'bootstrap'; shutil.copytree(evidence,bootstrap/'evidence')
        write_json(bootstrap/'stage.json',stage)
    write_json(public/'stage.json',stage)
    write_json(public/'dataset.json',dataset)
    write_json(root/'prepared.json',{'stage':stage['id'],'books':replay['books'],'source_tokens':replay['tokens'],
        'split_windows':{s:len(dataset[s]) for s in ('train','validation','test')},
        'split_books':{s:sorted({r['book'] for r in dataset[s]}) for s in ('train','validation','test')},
        'planned_sequences':len(selected),'planned_target_tokens':sum(r['count'] for r in selected),
        'trainable_parameters':len(adapter)//4,'adapter_bytes':len(adapter),'fixed_projection_bytes':len(projection),
        'base_parameters':sum(p.numel() for p in model.parameters())-len(adapter)//4})
    write_json(public/'artifact-index.json',{p.name:{'sha256':p.name,'bytes':p.stat().st_size} for p in ws.objects.root.iterdir() if len(p.name)==64})
    print(json.dumps(read_json(root/'prepared.json')),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['prepare'])
    p.add_argument('--root',type=Path,default=Path('/pilot/state')); p.add_argument('--repository',type=Path,default=Path('/repo'))
    p.add_argument('--source',type=Path,default=Path('/source')); a=p.parse_args()
    prepare(a.root,a.repository,a.source)
