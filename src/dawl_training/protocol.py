"""No central aggregator: every participant verifies and reduces the same updates.

This admitted, unanimous round protocol is a local training branch, not a new
Oracle consensus implementation or a proof that remote training was performed.
"""
import math
from pathlib import Path
import numpy as np
from dawl_node.common import canonical_json, sha256, sign, verify_signature
from dawl_profiles.profiles import content_id, descriptor, validate_profile


def implementation():
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'src').rglob('*.py'))
    paths.extend(root/'scripts'/name for name in ('training_peer.py','training_pilot.py','profile_gpu_pilot.py','cuda_gather.py'))
    return content_id({p.relative_to(root).as_posix():sha256(p.read_bytes()) for p in sorted(paths)})


def validate_stage(stage):
    b=stage['body']
    if set(stage)!={'id','body'} or stage['id']!=content_id(b) or b['schema']!='hypertrix.training-stage/1':
        raise ValueError('stage content identity mismatch')
    validate_profile(b['profile'])
    if b['profile']['body']['model']['family']!='llama': raise ValueError('unsupported training model')
    peers=b['participants']
    if not 2<=len(peers)<=8 or len(set(p['public'] for p in peers))!=len(peers):
        raise ValueError('stage requires distinct admitted peers')
    if [p['id'] for p in peers]!=list(range(len(peers))): raise ValueError('noncanonical participant order')
    for p in peers:
        if len(p['public'])!=64: raise ValueError('invalid participant identity')
    recipe=b['recipe']
    if (recipe['algorithm']!='fixed-A-LoRA/local-SGD/unanimous-token-weighted-mean/v1'
            or recipe['optimizer']!='SGD/no-momentum/no-weight-decay'
            or recipe['dtype']!='float32' or recipe['rank']!=8 or recipe['alpha']!=16
            or recipe['targets']!=['q_proj','v_proj'] or recipe['seed']!=20261014
            or recipe['sequence_length']!=128 or recipe['batch_size']!=2
            or not 1<=recipe['rounds']<=128 or not 1<=recipe['local_steps']<=8
            or not 0<recipe['learning_rate']<=0.2 or recipe['clip_grad_norm']!=1.0
            or recipe['max_update_l2']!=2.0):
        raise ValueError('unsupported bounded training recipe')
    for k in ('dataset','initial_adapter','fixed_projection'): descriptor(b[k])
    validate_layout(b['adapter_layout'],b['initial_adapter']['bytes'])
    validate_layout(b['projection_layout'],b['fixed_projection']['bytes'])
    return b


def validate_layout(layout,size):
    offset=0; names=[]
    for item in layout:
        shape=item['shape']
        if (set(item)!={'name','shape','offset','elements'} or len(shape)!=2
                or any(type(n)is not int or n<=0 for n in shape)
                or item['elements']!=math.prod(shape) or item['offset']!=offset):
            raise ValueError('invalid adapter layout')
        names.append(item['name']); offset+=item['elements']*4
    if not names or names!=sorted(set(names)) or offset!=size or size>16*1024**2:
        raise ValueError('adapter layout size/order mismatch')


def schedule(dataset,stage,round_number,peer_id):
    recipe=stage['body']['recipe']; peers=len(stage['body']['participants'])
    if not 0<=round_number<recipe['rounds'] or not 0<=peer_id<peers: raise ValueError('invalid work allocation')
    width=recipe['batch_size']*recipe['local_steps']
    start=(round_number*peers+peer_id)*width
    records=dataset['train'][start:start+width]
    if len(records)!=width: raise ValueError('stage exceeds unique training data')
    return records


def allocation(stage,dataset,round_number,peer_id,parent):
    records=schedule(dataset,stage,round_number,peer_id)
    return {'schema':'hypertrix.training-allocation/1','stage':stage['id'],'round':round_number,
        'peer':peer_id,'parent':parent,'data':content_id(records),
        'sequences':len(records),'target_tokens':sum(r['count'] for r in records),
        'local_steps':stage['body']['recipe']['local_steps']}


def validate_update(envelope,stage,dataset,round_number,peer_id,parent):
    public=stage['body']['participants'][peer_id]['public']
    body=verify_signature(envelope,public)
    if (set(body)!={'schema','signer','allocation','adapter','loss','seconds','device'}
            or body['schema']!='hypertrix.training-update/1'
            or body['allocation']!=allocation(stage,dataset,round_number,peer_id,parent)):
        raise ValueError('update uses wrong stage, parent or data allocation')
    descriptor(body['adapter'])
    if body['adapter']['bytes']!=stage['body']['initial_adapter']['bytes']:
        raise ValueError('update layout length differs')
    if not all(type(body[k]) in (int,float) and math.isfinite(body[k]) and body[k]>=0 for k in ('loss','seconds')):
        raise ValueError('invalid training metric')
    return body


def aggregate(stage,dataset,round_number,parent,previous,updates,objects):
    """Canonical CPU float64 accumulation, then one float32 rounding.

    A is fixed and shared, so averaging B is also averaging the effective BA
    matrices. This is local-SGD, not an equivalence to multi-step global SGD.
    """
    if len(updates)!=len(stage['body']['participants']): raise ValueError('incomplete update roster')
    base=np.frombuffer(objects.read(previous),dtype='<f4')
    if not np.isfinite(base).all(): raise ValueError('nonfinite parent adapter')
    total=sum(allocation(stage,dataset,round_number,i,parent)['target_tokens'] for i in range(len(updates)))
    result=np.zeros(len(base),dtype=np.float64)
    for peer,envelope in enumerate(updates):
        body=validate_update(envelope,stage,dataset,round_number,peer,parent)
        values=np.frombuffer(objects.read(body['adapter']),dtype='<f4')
        if (len(values)!=len(base) or not np.isfinite(values).all()
                or np.linalg.norm(values.astype(np.float64)-base)>stage['body']['recipe']['max_update_l2']):
            raise ValueError('nonfinite or out-of-policy adapter update')
        result+=values.astype(np.float64)*(body['allocation']['target_tokens']/total)
    payload=result.astype('<f4').tobytes()
    if not np.isfinite(np.frombuffer(payload,dtype='<f4')).all(): raise ValueError('nonfinite aggregate')
    return payload


def round_body(stage,round_number,parent,updates,adapter):
    return {'schema':'hypertrix.weight-round/1','stage':stage['id'],'round':round_number,
        'parent':parent,'updates':[u['id'] for u in updates],'adapter':adapter,
        'target_tokens':sum(u['body']['allocation']['target_tokens'] for u in updates)}


def certify(body,votes,stage):
    if len(votes)!=len(stage['body']['participants']): raise ValueError('incomplete checkpoint acceptance')
    for participant,vote in zip(stage['body']['participants'],votes):
        actual=verify_signature(vote,participant['public'])
        if actual!={**body,'signer':participant['public']}: raise ValueError('conflicting checkpoint acceptance')
    value={'body':body,'acceptances':votes}
    return {'id':content_id(value),**value}


def verify_round(record,stage,dataset,round_number,parent,previous,updates,objects):
    payload=aggregate(stage,dataset,round_number,parent,previous,updates,objects)
    adapter={'sha256':sha256(payload),'bytes':len(payload)}
    expected=round_body(stage,round_number,parent,updates,adapter)
    if record!=certify(expected,record['acceptances'],stage): raise ValueError('weight ledger differs from recomputed round')
    return adapter
