"""Offline verification of protocol-6 recovery evidence and full signed history."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import read_json, canonical_json, sha256
from dawl_cluster.state import initial, advance, evaluate
from dawl_cluster.training_protocol import identity
from dawl_platform.workflow import implementation_digest


def require(value, message):
    if not value: raise ValueError(message)


def verify(root, pin):
    proof=subprocess.run(['check-certificate','-genesis',str(root/'genesis.json'),'-genesis-sha256',pin,
                          '-certificate',str(root/'training-certificate.json')],capture_output=True,text=True,check=True)
    certificate=read_json(root/'training-certificate.json'); expected=certificate['state']
    history=read_json(root/'application-history.json'); trial=read_json(root/'trial.json')
    require(pin==trial['genesis_sha256'],'wrong external genesis pin')
    require(history['height']==expected['height'],'history height mismatch')
    require(all(str(int(h))==h and 1<=int(h)<=history['height'] for h in history['blocks']),'invalid block catalog')
    state=initial(read_json(root/'genesis.json')['app_state']); count=0; late=False
    late_vote=read_json(root/'late-vote.json')
    for height in range(1,history['height']+1):
        advance(state,height)
        for tx in history['blocks'].get(str(height),[]):
            state,result=evaluate(state,tx); count+=1
            if result['code']: state['rejected_transactions']+=1
        if height==late_vote['height']:
            unchanged,result=evaluate(state,late_vote['transaction'])
            late=result['code']==1 and unchanged==state
    require(canonical_json(state)==canonical_json(expected),'full state replay differs from certified state')
    require(late,'late vote rejection not reproduced')
    require(state['training_recovery_protocol']['implementation_sha256']==implementation_digest()==trial['implementation_sha256'],
            'execution source differs from activated implementation')
    stages={label:state['training_stages'][read_json(root/f'stage-{label}.json')['id']] for label in 'abc'}
    a,b,c=(stages[label] for label in 'abc')
    require([s['status'] for s in (a,b,c)]==['complete','cancelled','complete'],'wrong final stage states')
    require(len(a['checkpoints'])==len(c['checkpoints'])==1 and not b['checkpoints'],'wrong checkpoint count')
    require(b['head']==a['head']==c['body']['parent_checkpoint'],'cancelled stage advanced parent')
    require(c['body']['recovery']['supersedes']==b['id'],'replacement not bound to cancelled stage')
    require(a['recovery_revision']==1 and len(a['recovery_history'])==1,'expected single reassignment')
    event=a['recovery_history'][0]; report=a['failure_reports'][event['report']]
    require(report['reason']=='artifact_unavailable' and event['contribution']['transaction']==report['contribution'],
            'rejection not bound to unavailable update')
    job=state['training_jobs'][report['job']]
    require(job['contribution']['signer']==trial['identities']['trainer-2'] and job['attempts']==2
            and job['contribution']['generation']!=event['contribution']['generation'],'replacement did not reacquire shard')
    require(event['contribution']['signer']==trial['identities']['trainer-0'],'wrong withheld trainer')
    cancellation=b['recovery_history'][-1]
    require(cancellation['kind']=='cancel_training_stage' and cancellation['reason']=='round_timeout'
            and cancellation['height']>=b['round_started_height']+b['body']['recovery']['round_timeout_blocks'],
            'cancellation before immutable deadline')
    require(len(b['votes'])==1 and trial['identities']['verifier-2'] in b['body']['verifiers']
            and trial['identities']['verifier-2'] not in b['votes'],'missing verifier test invalid')
    require(c['body']['verifiers']==a['body']['verifiers'] and c['body']['verifiers']!=b['body']['verifiers'],
            'expected explicit replacement roster')
    checks=0
    for label,s in stages.items():
        require({'id':s['id'],'body':s['body']}==read_json(root/f'stage-{label}.json'),'stage envelope mismatch')
        data=(root/f'manifest-{label}.json').read_bytes()
        require(s['body']['manifest']=={'sha256':sha256(data),'bytes':len(data)},'manifest commitment mismatch')
        for r in s['failure_reports'].values():
            data=(root/'objects'/r['evidence']).read_bytes(); evidence=json.loads(data)
            require(sha256(data)==r['evidence'] and all(evidence[k]==r[k] for k in
                    ('stage','round','revision','job','contribution','reason')),'failure evidence binding differs')
        for name in ('verifier-0','verifier-1'):
            for path in (root/'workers'/label/name/'public').glob('replay-*.json'):
                record=read_json(path); key=trial['identities'][name]
                if label=='b': vote=b['votes'][key]
                else: vote=state['training_checkpoints'][s['head']]['receipts'][key]
                require(record['candidate']==vote['candidate'],'worker replay differs from signed vote')
                updates=record['candidate']['updates']
                require(len(record['checks'])==2 and [r['update'] for r in record['checks']]==updates
                        and all(r['bit_exact'] is True for r in record['checks']),'incomplete numerical replay receipt')
                for check in record['checks']:
                    require(state['training_jobs'][check['job']]['contribution']['transaction']==check['update'],
                            'replay check not bound to exact job')
                checks+=len(record['checks'])
    require(checks==10,'missing numerical replay records')
    for s in (a,c):
        checkpoint=state['training_checkpoints'][s['head']]
        require(checkpoint['id']==identity({k:checkpoint[k] for k in ('body','receipts')}),'checkpoint identity differs')
        require(set(checkpoint['receipts'])==set(s['body']['verifiers']) and len(checkpoint['receipts'])==2,
                'checkpoint lacks full named quorum')
    events=[]
    for path in (root/'workers').rglob('events.jsonl'):
        events.extend(json.loads(line) for line in path.read_text().splitlines())
    trained=[e for e in events if e['event']=='contributed']
    require(len(trained)==7,'expected seven actual GPU contributions including withheld and cancelled work')
    branch=state['model_branches'][a['body']['branch']]
    require(branch['head']==c['head'] and branch['checkpoints']==[a['head'],c['head']] and branch['active_stage'] is None,
            'final branch ancestry differs')
    return {'schema':'hypertrix.training-recovery-verification/1','quorum_certificate':json.loads(proof.stdout),
        'exact_state_match':True,'blocks_replayed':state['height'],'signed_transactions_replayed':count,
        'recovery_protocol':6,'stages':{k:s['status'] for k,s in stages.items()},'checkpoints':2,
        'actual_gpu_contributions':len(trained),'numerical_replay_checks':checks,'accepted_checkpoint_replay_checks':8,
        'withheld_update_reassigned':True,'missing_verifier_cancelled':True,'verified_parent_preserved':True,
        'late_vote_rejection_replayed':late,'replacement_full_quorum':True,'final_checkpoint':c['head'],
        'training_compute_seconds':sum(e['seconds'] for e in trained),
        'scope':'Offline signature, state-transition, artifact-binding and receipt verification. Numerical replay was executed by two worker processes on the same GPU and operator.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,required=True)
    p.add_argument('--genesis-sha256',required=True); a=p.parse_args()
    print(json.dumps(verify(a.root,a.genesis_sha256),indent=2))
