"""Small, deployment-independent presentations of ledger membership and observer state."""
from datetime import datetime,timezone
import base64
from collections import Counter
import re

ROLES={'oracle':'Oracle','cortex':'Cortex Unit','trainer':'Trainer','gpu-verifier':'Replay Daemon',
       'token-auditor':'Audit Daemon','discovery':'Discovery','console':'Console',
       'local-adaptation':'Fine-tuning','local-replay':'Replay','publisher':'Publisher'}


def clean(value):return re.sub(r'[\x00-\x1f\x7f-\x9f]','',str(value))[:160]


def display_name(node):
    name=node.get('name','');role=node.get('role','node');match=re.search(r'-(\d+)$',name)
    number=str(int(match[1])+1) if match and not name.startswith('hypertrix-') else ''
    if name.startswith('hypertrix-'):
        number=(node.get('id') or node.get('public') or '')[:5]
    return (ROLES.get(role,'Node')+' '+number).strip()


def phase(node):
    if not node.get('running'):return 'Complete' if node.get('exit_code')==0 else 'Stopped'
    if node.get('final'):return 'Serving checkpoint'
    events=node.get('events') or []
    event=(events[-1].get('event') or '') if events else ''
    return {'round_committed':'Checkpoint accepted','contribution_accepted':'Contribution accepted',
            'waiting':'Ready','training':'Training','replaying':'Verifying','replay_complete':'Replay complete'}.get(event,'Ready')


def public_network(state,genesis):
    nodes=[]
    for i,v in enumerate(genesis.get('validators',[])):
        nodes.append({'name':f'Oracle {i+1}','role':'oracle','identity':base64.b64decode(v['pub_key']['value']).hex(),'status':'Validator'})
    for i,(key,p) in enumerate(sorted(state.get('providers',{}).items())):
        nodes.append({'name':f'Cortex {i+1}','role':'cortex','identity':key,'status':'Registered'})
    counts=Counter()
    jobs=list(state.get('training_jobs',{}).values());checkpoints=list(state.get('training_checkpoints',{}).values())
    for key,m in sorted(state.get('training_members',{}).items(),key=lambda item:(item[1].get('height',0),item[0])):
        role='trainer' if m['role']=='TRAINER' else 'gpu-verifier';counts[role]+=1
        n={'name':('Trainer' if role=='trainer' else 'Replay Daemon')+' '+str(counts[role]),'role':role,'identity':key,'status':'Admitted'}
        n['accepted_contributions']=sum(j.get('contribution',{}).get('signer')==key for j in jobs if j.get('contribution'))
        n['receipts']=sum(key in c.get('receipts',{}) for c in checkpoints);nodes.append(n)
    for key,a in sorted(state.get('plan',{}).get('actors',{}).items()):
        role='token-auditor' if 'AUDIT_CPU' in a.get('hats',[]) else 'publisher';counts[role]+=1
        nodes.append({'name':('Audit Daemon' if role=='token-auditor' else 'Publisher')+' '+str(counts[role]),'role':role,'identity':key,'status':'Registered'})
    return {'nodes':nodes,'roles':dict(Counter(n['role'] for n in nodes)),
            'accepted_contributions':sum(bool(j.get('contribution')) for j in jobs),
            'accepted_artifact_bytes':sum(j['contribution'].get('adapter',{}).get('bytes',0) for j in jobs if j.get('contribution')),
            'completed_target_tokens':sum(j['spec'].get('target_tokens',0) for j in jobs if j.get('contribution')),
            'checkpoint_receipts':sum(len(c.get('receipts',{})) for c in checkpoints),
            'checkpoints':len(checkpoints),'pending_jobs':sum(not j.get('contribution') for j in jobs),
            'scope':'Certified membership and accepted work. Membership does not assert current process liveness.'}


def view(payload,now=None):
    now=now or datetime.now(timezone.utc)
    if payload.get('loading'):raise ValueError('Observer is loading')
    if payload.get('schema')=='hypertrix.public-read-replica/1':
        age=(now-datetime.fromisoformat(payload['header_time'].replace('Z','+00:00'))).total_seconds()
        if payload.get('verification',{}).get('verified') is not True:raise ValueError('Publication lacks verification')
        network=payload.get('network',{});nodes=network.get('nodes',[])
        return {'health':'Ledger synced' if -30<=age<=180 else 'Updates paused','fresh':-30<=age<=180,
                'height':payload['height'],'age':max(0,age),'nodes':nodes,'history':[],
                'work':network,'metrics':{},'gpu':None,'stages':payload.get('stages',[]),'mode':'membership',
                'chain':payload['chain_id'],'genesis':payload['genesis_sha256'],'errors':[],
                'verification':'Publisher-checked quorum certificate; membership is not node liveness.'}
    if 'certified' not in payload:raise ValueError('Unknown monitor response')
    s=payload.get('state') or payload.get('last_verified_state') or {};tele=payload.get('telemetry') or {}
    observed=tele.get('observed_at');age=(now-datetime.fromisoformat(observed)).total_seconds() if observed else None
    fresh=age is not None and -5<=age<=30
    nodes=[];history=[]
    for n in tele.get('nodes',[]):
        presented={'name':display_name(n),'role':n['role'],'status':phase(n) if fresh else 'Last seen',
                   'identity':n.get('public') or n.get('id') or '', 'running':bool(n.get('running')) and fresh,
                   'contacts':(n.get('dht') or {}).get('contacts'),'raw':n}
        (nodes if n.get('running') and not n.get('historical_attempt') else history).append(presented)
    jobs=list(s.get('training_jobs',{}).values());checkpoints=list(s.get('training_checkpoints',{}).values())
    work={'accepted_contributions':sum(bool(j.get('contribution')) for j in jobs),'pending_jobs':sum(not j.get('contribution') for j in jobs),
          'checkpoints':len(checkpoints),'checkpoint_receipts':sum(len(c.get('receipts',{})) for c in checkpoints),
          'completed_target_tokens':sum(j['spec'].get('target_tokens',0) for j in jobs if j.get('contribution'))}
    return {'health':'Network synced' if payload['certified'] and fresh else 'Updates paused','fresh':fresh and payload['certified'],
            'height':s.get('height',0),'age':age,'nodes':nodes,'history':history,'work':work,
            'metrics':tele.get('aggregate',{}) if fresh else {},'gpu':tele.get('gpu') if fresh else None,
            'stages':list(s.get('training_stages',{}).values()),'mode':'observer',
            'chain':s.get('chain_id'),'genesis':payload.get('genesis_sha256'),'errors':payload.get('errors',[]),
            'verification':'Quorum verified by the observer. Resource counters are operational measurements.'}
