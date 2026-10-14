"""Independent trainer, aggregator, replica, or numerical replay auditor."""
import argparse
from datetime import datetime,timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError,URLError
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src')); sys.path.insert(0,str(ROOT/'scripts'))
from dawl_node.common import read_json,write_json,sign,load_key,verify_signature,sha256,canonical_json
from dawl_profiles.profiles import content_id,describe
from dawl_profiles.workspace import Workspace
from dawl_training.data import make_dataset
from dawl_training.protocol import (validate_stage,schedule,allocation,validate_update,aggregate,
    round_body,certify,verify_round,implementation)
from dawl_training.network import Network,serve
from profile_gpu_pilot import certificate,lineage


def wait_public(url,route,timeout=900):
    from dawl_platform.artifacts import PublicPeer
    peer=PublicPeer(url,True); started=time.monotonic()
    while time.monotonic()-started<timeout:
        try: return peer.json(route)
        except (HTTPError,URLError,OSError,TimeoutError): time.sleep(0.5)
    raise TimeoutError('waiting for public stage record '+route)


def bootstrap(root):
    stage=read_json(root/'bootstrap/stage.json'); b=validate_stage(stage)
    if b['implementation_sha256']!=implementation(): raise ValueError('training code differs from announced stage')
    ws=Workspace(root/'public')
    urls=[p['url'] for p in b['participants']]+[b['provider']]
    network=Network(ws.objects,urls,root/'public/transfers.jsonl')
    state,cert=certificate(root/'bootstrap/evidence')
    if cert!=b['source_certificate']: raise ValueError('wrong pinned source certificate')
    profile,head,entries,replay=lineage(ws,state,'smollm2',network.fetch)
    if profile!=b['profile'] or head['id']!=b['token_blueprint']: raise ValueError('wrong certified training source')
    network.fetch(b['dataset']); dataset=ws.objects.read_json(b['dataset'])
    if dataset!=make_dataset(ws.objects,entries): raise ValueError('training windows differ from source lineage')
    for desc in b['parent_model']['chunks']+[b['initial_adapter'],b['fixed_projection']]: network.fetch(desc)
    for library,expected in b['runtime'].items():
        if version(library)!=expected: raise ValueError('training runtime differs: '+library)
    from dawl_training.model import assemble,load_model,restore
    path=assemble(ws.objects,b['parent_model'],root/'model/model.safetensors')
    model=load_model(ws.objects.read(profile['body']['model']['config']),path)
    restore(model,ws.objects.read(b['fixed_projection']),b['projection_layout'],'A')
    restore(model,ws.objects.read(b['initial_adapter']),b['adapter_layout'])
    return stage,dataset,ws,network,model


def evaluate_splits(model,dataset,objects,device):
    from dawl_training.model import evaluate
    return {s:evaluate(model,dataset[s][:16],objects,device) for s in ('train','validation','test')}


def run(root,peer_id,device,stop_after_update=None):
    from dawl_training.model import train_local,restore,export
    import torch
    started=time.monotonic(); stage,dataset,ws,network,model=bootstrap(root); b=stage['body']
    key=load_key(root/'key.private.pem'); public=key.public_key().public_bytes_raw().hex()
    if b['participants'][peer_id]['public']!=public: raise ValueError('wrong training identity')
    if device.startswith('cuda') and not torch.cuda.is_available(): raise ValueError('CUDA required; fallback disabled')
    model.to(device)
    endpoint=urlsplit(b['participants'][peer_id]['url'])
    server=serve(root/'public',endpoint.hostname,endpoint.port)
    print(json.dumps({'event':'joined','stage':stage['id'],'peer':peer_id,'device':device}),flush=True)
    baseline=root/'public/baseline.json'
    if not baseline.exists(): write_json(baseline,evaluate_splits(model,dataset,ws.objects,device))
    parent=stage['id']; previous=b['initial_adapter']; completed=0; reused=0
    for r in range(b['recipe']['rounds']):
        number=f'{r:03d}.json'; location=root/'public'
        announce=sign({'schema':'hypertrix.training-announcement/1',
                       'allocation':allocation(stage,dataset,r,peer_id,parent)},key)
        write_json(location/'announce'/number,announce)
        update_path=location/'update'/number
        if update_path.exists():
            update=read_json(update_path); validate_update(update,stage,dataset,r,peer_id,parent); reused+=1
        else:
            restore(model,ws.objects.read(previous),b['adapter_layout']); before=time.perf_counter()
            records=schedule(dataset,stage,r,peer_id)
            loss=train_local(model,records,ws.objects,b['recipe'],device)
            layout,payload=export(model)
            if layout!=b['adapter_layout']: raise ValueError('training changed adapter layout')
            update=sign({'schema':'hypertrix.training-update/1','allocation':allocation(stage,dataset,r,peer_id,parent),
                'adapter':ws.objects.put(payload),'loss':loss,'seconds':time.perf_counter()-before,'device':device},key)
            write_json(update_path,update)
            print(json.dumps({'event':'update','peer':peer_id,'round':r,'loss':loss,'artifact':update['body']['adapter']}),flush=True)
        if stop_after_update==r:
            print(json.dumps({'event':'injected_exit_after_durable_update','round':r,'peer':peer_id}),flush=True)
            server.shutdown(); return 75
        updates=[]
        for participant in b['participants']:
            other=participant['id']
            envelope=update if other==peer_id else network.record(participant['url'],'update',r)
            claim=validate_update(envelope,stage,dataset,r,other,parent)
            network.fetch(claim['adapter']); updates.append(envelope)
        payload=aggregate(stage,dataset,r,parent,previous,updates,ws.objects)
        adapter=ws.objects.put(payload); body=round_body(stage,r,parent,updates,adapter)
        vote=sign(body,key); write_json(location/'accept'/number,vote)
        votes=[vote if p['id']==peer_id else network.record(p['url'],'accept',r) for p in b['participants']]
        record=certify(body,votes,stage); write_json(location/'ledger'/number,record)
        ws.objects.json(record)
        for i,u in enumerate(updates): write_json(location/'received'/f'{r:03d}-{i}.json',u)
        previous=adapter; parent=record['id']; completed+=1
        print(json.dumps({'event':'committed','peer':peer_id,'round':r,'checkpoint':parent}),flush=True)
    restore(model,ws.objects.read(previous),b['adapter_layout'])
    final=sign({'schema':'hypertrix.training-completion/1','stage':stage['id'],'peer':peer_id,'checkpoint':parent,
        'adapter':previous,'rounds':completed,'baseline':read_json(baseline),
        'evaluation':evaluate_splits(model,dataset,ws.objects,device),'artifact_payload_bytes':network.bytes,
        'artifact_requests':network.requests,'wait_retries_this_process':network.waits,
        'reused_updates_this_process':reused,'seconds_this_process':time.monotonic()-started,
        'runtime':{'torch':torch.__version__,'device':torch.cuda.get_device_name() if device.startswith('cuda') else 'CPU',
                   'deterministic_algorithms':torch.are_deterministic_algorithms_enabled()},
        'scope':'Local unanimous adapter branch; all participants retain publishable base, adapters and weight ledger. No Oracle promotion.'},key)
    write_json(location/'final.json',final)
    print(json.dumps({'event':'finished','peer':peer_id,'checkpoint':parent,'evaluation':final['body']['evaluation']}),flush=True)
    audit_receipt=wait_public(b['auditor_url'],'/audit')
    checked=verify_signature(audit_receipt,b['auditor'])
    if (checked['schema']!='hypertrix.training-audit/1' or checked['stage']!=stage['id']
            or checked['checkpoint']!=parent or checked['rounds_verified']!=completed
            or checked['replicas_identical'] is not True or checked['aggregation_recomputed'] is not True
            or len(checked['numerical_training_replays'])!=2
            or not all(c['bit_exact'] is True for c in checked['numerical_training_replays'])
            or checked['evaluation']!=final['body']['evaluation']): raise ValueError('stage audit does not justify this release')
    release_body={'schema':'hypertrix.training-release/1','stage':stage['id'],'checkpoint':parent,
        'audit':audit_receipt['id'],'profile_id':b['profile']['id'],'parent_model':b['parent_model']['sha256'],
        'fixed_projection':b['fixed_projection'],'adapter':previous,
        'policy':'Local admitted stage; full reduction verification and two post-commit numerical samples. Not Oracle promotion.'}
    release_vote=sign(release_body,key); write_json(location/'release-vote.json',release_vote)
    release_votes=[release_vote if p['id']==peer_id else wait_public(p['url'],'/release-vote') for p in b['participants']]
    release=certify(release_body,release_votes,stage); write_json(location/'release.json',release)
    write_json(location/'audit.json',audit_receipt)
    print(json.dumps({'event':'released','peer':peer_id,'release':release['id']}),flush=True)
    # Remain an artifact replica after training. The pilot operator stops it after audit.
    while True: time.sleep(1)


def audit(root,device):
    from dawl_training.model import train_local,restore,export
    from dawl_platform.artifacts import PublicPeer
    import torch
    stage,dataset,ws,network,model=bootstrap(root); b=stage['body']; model.to(device)
    parent=stage['id']; previous=b['initial_adapter']; chain=[]; updates_by_round=[]; snapshots=[]
    for r in range(b['recipe']['rounds']):
        updates=[network.record(p['url'],'update',r) for p in b['participants']]
        records=[network.record(p['url'],'ledger',r) for p in b['participants']]
        if records[0]!=records[1]: raise ValueError('replicated weight histories diverged')
        for p,u in enumerate(updates):
            claim=validate_update(u,stage,dataset,r,p,parent); network.fetch(claim['adapter'])
        adapter=verify_round(records[0],stage,dataset,r,parent,previous,updates,ws.objects)
        network.fetch(adapter); snapshots.append((parent,previous)); previous=adapter; parent=records[0]['id']
        chain.append(records[0]); updates_by_round.append(updates)
    final=[]
    for p in b['participants']:
        receipt=PublicPeer(p['url'],True).json('/final'); claim=verify_signature(receipt,p['public'])
        if claim['stage']!=stage['id'] or claim['checkpoint']!=parent or claim['adapter']!=previous:
            raise ValueError('completion refers to wrong branch head')
        final.append(receipt)
    # Select after all updates and final checkpoint are committed. This is a local
    # auditor nonce, not decentralized randomness or a proof of useful work.
    challenge_path=root/'challenge.json'
    if not challenge_path.exists():
        nonce=secrets.token_hex(32)
        choices=sorted([(r,p) for r in range(b['recipe']['rounds']) for p in range(2)],
            key=lambda x:sha256(canonical_json({'nonce':nonce,'head':parent,'round':x[0],'peer':x[1]})))[:2]
        write_json(challenge_path,{'nonce':nonce,'committed_head':parent,'samples':choices})
    challenge=read_json(challenge_path); checks=[]
    if challenge['committed_head']!=parent: raise ValueError('audit challenge belongs to different head')
    for r,p in challenge['samples']:
        restore(model,ws.objects.read(snapshots[r][1]),b['adapter_layout'])
        train_local(model,schedule(dataset,stage,r,p),ws.objects,b['recipe'],device)
        _,payload=export(model); expected=updates_by_round[r][p]['body']['adapter']
        if describe(payload)!=expected: raise ValueError('independent training replay differs from announced contribution')
        checks.append({'round':r,'peer':p,'bit_exact':True,'adapter':expected})
        print(json.dumps({'event':'replayed_training','round':r,'peer':p,'bit_exact':True}),flush=True)
    restore(model,ws.objects.read(previous),b['adapter_layout'])
    evaluation=evaluate_splits(model,dataset,ws.objects,device)
    if evaluation!=final[0]['body']['evaluation'] or evaluation!=final[1]['body']['evaluation']:
        raise ValueError('independent final evaluation differs')
    result=sign({'schema':'hypertrix.training-audit/1','stage':stage['id'],'checkpoint':parent,
        'rounds_verified':len(chain),'replicas_identical':True,'aggregation_recomputed':True,
        'challenge':challenge,'numerical_training_replays':checks,'evaluation':evaluation,
        'scope':'Two post-commit sampled local training replays; full aggregation/signature checks. Same operator and GPU; no proof of every update, WAN performance or upstream pretraining.'},load_key(root/'key.private.pem'))
    write_json(root/'audit.json',result); write_json(root/'public/audit.json',result)
    endpoint=urlsplit(b['auditor_url']); server=serve(root/'public',endpoint.hostname,endpoint.port)
    write_json(root/'chain.json',chain); write_json(root/'updates.json',updates_by_round)
    write_json(root/'completions.json',final)
    releases=[wait_public(p['url'],'/release') for p in b['participants']]
    if releases[0]!=releases[1] or releases[0]['body']['audit']!=result['id']:
        raise ValueError('release replicas disagree')
    if certify(releases[0]['body'],releases[0]['acceptances'],stage)!=releases[0]: raise ValueError('release signatures differ')
    write_json(root/'release.json',releases[0]); server.shutdown()
    print(json.dumps({'event':'audit_complete','checkpoint':parent,'rounds':len(chain)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['run','audit']); p.add_argument('--root',type=Path,default=Path('/node'))
    p.add_argument('--peer',type=int,choices=[0,1]); p.add_argument('--device',default='cuda:0'); p.add_argument('--stop-after-update',type=int)
    a=p.parse_args()
    if a.command=='run': sys.exit(run(a.root,a.peer,a.device,a.stop_after_update))
    else: audit(a.root,a.device)
