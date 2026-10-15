"""Signed content-addressed lab packages and resumable, hash-checked peer transfer."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import time
import uuid
import urllib.request
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from dawl_node.common import canonical_json,keygen,load_key,sign,verify_signature,immutable_write,write_json
from dawl_scratch.data import file_hash
from .run import load_checkpoint,validate_lineage


def relative_path(value):
    p=PurePosixPath(value)
    if not value or p.is_absolute() or '\\' in value or ':' in value or any(v in ('','.','..') for v in value.split('/')):raise ValueError('Unsafe artifact path')
    if any(v.endswith(('.pem','.token','.key')) and v!='operator.public.key' for v in p.parts):raise ValueError('Private material is not publishable')
    return p


def publish(root,dataset,run,step):
    root.mkdir(parents=True,exist_ok=True);objects=root/'objects';objects.mkdir(exist_ok=True)
    if not (root/'publisher.private.pem').exists():keygen(root/'publisher.private.pem',root/'publisher.public.key')
    # Validate selected checkpoint before making it available to consumers.
    model,_,_=load_checkpoint(run,step);del model
    files={}
    for name in ('blueprint.json','sources.json','source-lock.json','corpus-plan.json','tokenizer.json','evaluation.json','windows.json','train.bin','validation.bin','test.bin'):
        files['dataset/'+name]=dataset/name
    for name in ('config.json','recipe.json','schedule.json','schedule.npy','operator.public.key','lineage.jsonl','implementation.json'):
        files['run/'+name]=run/name
    for path in (run/'implementation').rglob('*.py'):files['run/'+path.relative_to(run).as_posix()]=path
    steps={0,step};replay_intervals=[]
    for row in validate_lineage(run):
        if row['kind']!='replay':continue
        receipt_path=run/row['payload']['path']
        if file_hash(receipt_path)!=row['payload']['sha256']:raise ValueError('Replay evidence differs')
        receipt=json.loads(receipt_path.read_bytes())
        if receipt['accepted'] and receipt['end_step']==step:
            steps.add(receipt['start_step']);replay_intervals.append([receipt['start_step'],step])
            files['run/'+receipt_path.name]=receipt_path
            sample=receipt['resources']['samples_file']
            if Path(sample).name!=sample or file_hash(run/sample)!=receipt['resources']['samples_sha256']:raise ValueError('Replay resource evidence differs')
            files['run/'+sample]=run/sample
    for checkpoint_step in steps:
        # A DDP interval may not contain step zero; its parent is separately pinned.
        if checkpoint_step==0 and not (run/'checkpoints/0000000/checkpoint.json').exists():continue
        for name in ('checkpoint.json','state.safetensors'):
            files[f'run/checkpoints/{checkpoint_step:07d}/'+name]=run/'checkpoints'/f'{checkpoint_step:07d}'/name
    entries=[]
    for name,path in sorted(files.items()):
        relative_path(name)
        if path.is_symlink():raise ValueError('Symlink source rejected')
        digest=file_hash(path);target=objects/digest
        if not target.exists():
            # Snapshot even journals: a hard link would change when training appends.
            pending=objects/(digest+'.'+uuid.uuid4().hex+'.pending')
            with path.open('rb') as src,pending.open('xb') as dst:
                while block:=src.read(1024**2):dst.write(block)
            if file_hash(pending)!=digest:raise ValueError('Source changed during publication; retry after checkpoint')
            pending.replace(target)
        if file_hash(target)!=digest:raise ValueError('Stored artifact differs')
        entries.append({'path':name,'sha256':digest,'bytes':path.stat().st_size})
    body={'schema':'hypertrix.lab-package/1','created_at':datetime.now(timezone.utc).isoformat(),'checkpoint_step':step,
          'model_operator':(run/'operator.public.key').read_text().strip(),'blueprint_sha256':file_hash(dataset/'blueprint.json'),
          'files':entries,'replay_intervals':replay_intervals,'scope':'Full-model lab checkpoint, replay boundaries and token Blueprint; provenance signatures, not an Oracle certificate.'}
    envelope=sign(body,load_key(root/'publisher.private.pem'));write_json(root/'packages'/f'{envelope["id"]}.json',envelope)
    return {'package_id':envelope['id'],'publisher':(root/'publisher.public.key').read_text().strip(),'files':len(entries),'bytes':sum(e['bytes'] for e in entries)}


def app(root):
    root=root.resolve();api=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    def digest(value):
        if not re.fullmatch('[0-9a-f]{64}',value):raise HTTPException(400,'Invalid object identifier')
    @api.get('/health')
    def health():return {'role':'PROVIDER','packages':len(list((root/'packages').glob('*.json')))}
    @api.get('/packages/{identity}')
    def package(identity:str):
        digest(identity);path=root/'packages'/f'{identity}.json'
        if not path.is_file() or path.is_symlink():raise HTTPException(404,'Package unavailable')
        return FileResponse(path,media_type='application/json')
    @api.get('/objects/{identity}')
    def object_file(identity:str):
        digest(identity);path=root/'objects'/identity
        if not path.is_file() or path.is_symlink():raise HTTPException(404,'Object unavailable')
        # FileResponse supports HTTP ranges; consumers check the entire SHA-256.
        return FileResponse(path,media_type='application/octet-stream',headers={'ETag':'"'+identity+'"'})
    return api


def pull(root,url,identity,publisher):
    if not re.fullmatch('[0-9a-f]{64}',identity) or not re.fullmatch('[0-9a-f]{64}',publisher):raise ValueError('Invalid package/key pin')
    if not url.startswith(('http://','https://')):raise ValueError('HTTP(S) source required')
    with urllib.request.urlopen(url.rstrip('/')+'/packages/'+identity,timeout=30) as response:
        payload=response.read(1024**2+1)
        if len(payload)>1024**2:raise ValueError('Package manifest too large')
    envelope=json.loads(payload);body=verify_signature(envelope,publisher)
    if envelope['id']!=identity or body['schema']!='hypertrix.lab-package/1':raise ValueError('Package identity differs')
    if not 1<=len(body['files'])<=512 or sum(v['bytes'] for v in body['files'])>100*1024**3:raise ValueError('Package exceeds limits')
    root=root.resolve();root.mkdir(parents=True,exist_ok=True);seen=set();transfers=[]
    for item in body['files']:
        name=relative_path(item['path'])
        if name.as_posix() in seen:raise ValueError('Duplicate package path')
        seen.add(name.as_posix());target=root.joinpath(*name.parts)
        if not target.resolve().is_relative_to(root):raise ValueError('Artifact escapes destination')
        if not re.fullmatch('[0-9a-f]{64}',item['sha256']) or type(item['bytes']) is not int or item['bytes']<0:raise ValueError('Invalid object metadata')
        if target.exists():
            if target.is_symlink() or file_hash(target)!=item['sha256']:raise ValueError('Existing artifact differs; use another destination')
            continue
        target.parent.mkdir(parents=True,exist_ok=True);partial=target.with_name(target.name+'.partial')
        if partial.is_symlink():raise ValueError('Symlink partial file')
        offset=partial.stat().st_size if partial.exists() else 0
        if offset>item['bytes']:raise ValueError('Partial exceeds expected size')
        began=time.monotonic();received=0
        if offset<item['bytes']:
            request=urllib.request.Request(url.rstrip('/')+'/objects/'+item['sha256'],headers={'Range':f'bytes={offset}-'} if offset else {})
            with urllib.request.urlopen(request,timeout=90) as response:
                append=offset and response.status==206
                if append and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):raise ValueError('Unexpected range response')
                if not append:offset=0
                with partial.open('ab' if append else 'wb') as f:
                    while chunk:=response.read(1024**2):
                        received+=len(chunk)
                        if offset+received>item['bytes']:raise ValueError('Object exceeds expected bytes')
                        f.write(chunk)
        if not partial.exists() and item['bytes']==0:partial.touch()
        if partial.stat().st_size!=item['bytes'] or file_hash(partial)!=item['sha256']:raise ValueError('Downloaded object hash differs')
        partial.replace(target);transfers.append({'path':name.as_posix(),'bytes_received':received,'resumed_bytes':offset,'seconds':time.monotonic()-began})
    write_json(root/'package.json',envelope)
    receipt={'schema':'hypertrix.lab-transfer/1','package_id':identity,'accepted':True,'objects':len(body['files']),'transfers':transfers,
             'total_received_bytes':sum(v['bytes_received'] for v in transfers)}
    write_json(root/('transfer-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json'),receipt)
    return receipt


def verify_package(root,publisher,identity=None):
    """Verify a selected checkpoint package without requiring other checkpoints."""
    from .data import load
    envelope=json.loads((root/'package.json').read_bytes());body=verify_signature(envelope,publisher)
    if body['schema']!='hypertrix.lab-package/1' or (identity and identity!=envelope['id']):raise ValueError('Package identity differs')
    for entry in body['files']:
        name=relative_path(entry['path']);path=root.joinpath(*name.parts)
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size!=entry['bytes'] or file_hash(path)!=entry['sha256']:raise ValueError('Package file differs')
    dataset=root/'dataset';run=root/'run';bp,_=load(dataset)
    records=validate_lineage(run,body['model_operator']);first=records[0]['payload']
    if file_hash(dataset/'blueprint.json')!=body['blueprint_sha256'] or first['blueprint_sha256']!=body['blueprint_sha256']:raise ValueError('Package Blueprint differs')
    for name,key in [('config.json','config_sha256'),('recipe.json','recipe_sha256'),('implementation.json','implementation_sha256'),('schedule.json','schedule_manifest_sha256')]:
        if file_hash(run/name)!=first[key]:raise ValueError('Package initialization differs')
    for name,digest in json.loads((run/'implementation.json').read_bytes()).items():
        if file_hash(run/'implementation'/name)!=digest:raise ValueError('Frozen implementation differs')
    if file_hash(run/'schedule.npy')!=json.loads((run/'schedule.json').read_bytes())['sha256']:raise ValueError('Package schedule differs')
    model,_,desc=load_checkpoint(run,body['checkpoint_step']);del model
    return {'schema':'hypertrix.lab-package-verification/1','accepted':True,'package_id':envelope['id'],
            'checkpoint_step':desc['step'],'state_sha256':desc['state_sha256'],'files_hashed':len(body['files']),
            'scope':'Publisher signature, source metadata/token arrays, frozen recipe/code/schedule, operator lineage and selected checkpoint bytes. Numerical replay is a separate action.'}


def replicate(root,bundle,publisher):
    import shutil
    result=verify_package(bundle,publisher);body=json.loads((bundle/'package.json').read_bytes())['body']
    (root/'objects').mkdir(parents=True,exist_ok=True)
    for item in body['files']:
        target=root/'objects'/item['sha256']
        if not target.exists():
            pending=target.with_name(target.name+'.'+uuid.uuid4().hex+'.pending')
            shutil.copyfile(bundle/item['path'],pending)
            if file_hash(pending)!=item['sha256']:raise ValueError('Replica copy differs')
            pending.replace(target)
        if file_hash(target)!=item['sha256']:raise ValueError('Existing replica object differs')
    immutable_write(root/'packages'/(result['package_id']+'.json'),(bundle/'package.json').read_bytes())
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['publish','serve','pull','verify','replicate']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--dataset',type=Path);p.add_argument('--run',type=Path);p.add_argument('--step',type=int);p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=8911)
    p.add_argument('--url');p.add_argument('--package-id');p.add_argument('--publisher');p.add_argument('--bundle',type=Path);a=p.parse_args()
    if a.command=='publish':result=publish(a.root,a.dataset,a.run,a.step)
    elif a.command=='pull':result=pull(a.root,a.url,a.package_id,a.publisher)
    elif a.command=='verify':result=verify_package(a.root,a.publisher,a.package_id)
    elif a.command=='replicate':result=replicate(a.root,a.bundle,a.publisher)
    else:
        import uvicorn
        uvicorn.run(app(a.root),host=a.bind,port=a.port,access_log=False);raise SystemExit
    print(json.dumps(result))
