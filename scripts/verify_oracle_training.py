"""Verify an exported Oracle certificate and replay every application transition.

Requires the genesis hash through a trusted channel. This verifies signed lineage
and acceptance policy; it does not replace numerical tensor replay by verifiers.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'src'))
from dawl_node.common import read_json, canonical_json, sha256, verify_signature
from dawl_cluster.state import initial, advance, evaluate
from dawl_cluster.training_protocol import identity
from dawl_platform.workflow import implementation_digest


def verify(root, pin):
    output = subprocess.run(['check-certificate', '-genesis', str(root/'genesis.json'),
        '-genesis-sha256', pin, '-certificate', str(root/'training-certificate.json')],
        check=False, capture_output=True, text=True)
    if output.returncode:
        raise ValueError('certificate verification failed: ' + output.stderr.strip())
    proof = json.loads(output.stdout); certificate = read_json(root/'training-certificate.json')
    history = read_json(root/'application-history.json'); expected = certificate['state']
    if history['height'] != expected['height']: raise ValueError('history height differs from certificate')
    if any(str(int(h)) != h or not 1 <= int(h) <= history['height'] for h in history['blocks']):
        raise ValueError('invalid block catalog')
    churn = read_json(root/'churn.json')
    state = initial(read_json(root/'genesis.json')['app_state']); count = 0; late_rejected = False
    for h in range(1, history['height']+1):
        advance(state, h)
        for tx in history['blocks'].get(str(h), []):
            state, result = evaluate(state, tx); count += 1
            if result['code']: state['rejected_transactions'] += 1
        if h == churn['expired_height']:
            old_tx = churn['late_transaction']; job = state['training_jobs'][old_tx['body']['payload']['job']]
            if old_tx['body']['signer'] in job['leases'] or h <= churn['abandoned']['lease']['expires']:
                raise ValueError('claimed expiry is not present in replayed state')
            unchanged, rejected = evaluate(state, old_tx)
            late_rejected = rejected['code'] == 1 and unchanged == state
    if canonical_json(state) != canonical_json(expected): raise ValueError('replayed state differs from signed app hash')
    if state['training_protocol']['implementation_sha256'] != implementation_digest():
        raise ValueError('verification source differs from activated implementation')
    stage_file = read_json(root/'stage.json'); stage = state['training_stages'][stage_file['id']]
    if {k:stage[k] for k in ('id','body')} != stage_file or stage['status'] != 'complete':
        raise ValueError('exported stage not complete in certified state')
    manifest_bytes = (root/'manifest.json').read_bytes()
    if stage['body']['manifest'] != {'sha256':sha256(manifest_bytes),'bytes':len(manifest_bytes)}:
        raise ValueError('executable manifest differs from stage commitment')
    for checkpoint in state['training_checkpoints'].values():
        if checkpoint['id'] != identity({k:checkpoint[k] for k in ('body','receipts')}): raise ValueError('checkpoint identity differs')
    if not late_rejected: raise ValueError('late transaction rejection was not reproduced')
    old = verify_signature(churn['late_transaction'], churn['late_transaction']['body']['signer'])
    job = state['training_jobs'][old['payload']['job']]
    if (job['contribution']['signer'] != churn['replacement'] or job['attempts'] != 2
            or job['contribution']['generation'] == old['payload']['generation']):
        raise ValueError('replacement evidence differs from final state')
    return {'quorum_certificate':proof,'application_transitions_replayed':count,
            'blocks_replayed':state['height'],'exact_state_match':True,'stage':stage['id'],
            'checkpoints':len(stage['checkpoints']),'final_checkpoint':stage['head'],
            'replacement_completed_same_shard':True,
            'late_result_rejection_replayed':True,
            'scope':'Public signatures, complete deterministic state replay and quorum acceptance. Not independent numerical training replay.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--root',type=Path,required=True)
    p.add_argument('--genesis-sha256', required=True); a=p.parse_args()
    print(json.dumps(verify(a.root,a.genesis_sha256),indent=2))
