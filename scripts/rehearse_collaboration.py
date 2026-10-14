"""Export/replay a signed synthetic metadata rehearsal, not a GPU or consensus test.

Uses the repository's protocol fixtures. No cloud, network calls, persistent node
keys or live-chain writes. The output contains public keys and signed transactions.
"""
import argparse
import copy
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src')); sys.path.insert(0, str(ROOT/'tests'))
from dawl_cluster.state import initial, advance, evaluate
from dawl_cluster.collaboration import identity, select_checkpoint, summary
from dawl_node.common import sign, read_json, write_json, canonical_json
from dawl_platform.workflow import implementation_digest


def verify(directory):
    plan = read_json(directory/'plan.json'); transcript = read_json(directory/'transactions.json')
    state = initial(plan); accepted = rejected = 0
    for entry in transcript:
        height = entry['height']
        if type(height) is not int or height < state['height']: raise ValueError('invalid transcript height')
        while state['height'] < height: advance(state, state['height']+1)
        before = canonical_json(state); state, result = evaluate(state, entry['tx'])
        if result['code'] != entry['code']: raise ValueError('transcript acceptance differs')
        if result['code']:
            if canonical_json(state) != before: raise ValueError('rejected transaction mutated state')
            rejected += 1
        else: accepted += 1
    expected = read_json(directory/'state.json')
    if canonical_json(state) != canonical_json(expected): raise ValueError('replayed state differs')
    selections = read_json(directory/'selections.json')
    resolved = [select_checkpoint(state, selection) for selection in selections]
    if resolved != read_json(directory/'resolved.json'): raise ValueError('consumer selections differ')
    return {'scope': 'Synthetic signed metadata rehearsal; no tensor execution, DHT traffic or CometBFT certificate.',
            'accepted_transactions': accepted, 'rejected_transactions': rejected, 'state_sha256': identity(state),
            'consumer_selections_verified': len(resolved), 'exact_replay': True}


def generate(directory):
    if directory.exists(): raise ValueError('use a fresh output directory or --verify')
    from test_collaboration import CollaborationTests

    class Rehearsal(CollaborationTests):
        def send(self, key, kind, payload, ok=True, **extra):
            public = self.pub(key)
            body = {'schema': 'dawl.cluster-tx/1', 'chain_id': self.state['chain_id'],
                    'plan_sha256': self.state['plan_sha256'], 'sequence': self.state['actors'].get(public, {'sequence': 0})['sequence']+1,
                    'valid_until': self.state['height']+60, 'kind': kind, 'payload': payload, **extra}
            tx = sign(body, key); before = canonical_json(self.state)
            self.state, result = evaluate(self.state, tx)
            self.assertEqual(result['code'], 0 if ok else 1)
            if not ok: self.assertEqual(before, canonical_json(self.state))
            self.transcript.append({'height': self.state['height'], 'tx': copy.deepcopy(tx), 'code': result['code']})
            return result.get('data')

    case = Rehearsal(); case.transcript = []
    try:
        case.setUp()
        case.test_explicit_forks_can_diverge_and_leave_original_unchanged()
        state = case.state
        selections = [case.selection({'id': i, 'body': b['body']}, b['head']) for i, b in state['model_branches'].items()]
        # Attempt to substitute a sibling checkpoint under another branch pin.
        substitution = {**selections[1], 'checkpoint': selections[2]['checkpoint']}
        try: select_checkpoint(state, substitution)
        except ValueError: pass
        else: raise AssertionError('cross-branch consumer selection accepted')
        # Show on-chain policy rejection and unchanged original branch history.
        case.send(case.keys[3], 'register_training_stage', {'stage': case.legacy}, False)
        manifest = next(iter(state['collaborations'].values()))
        body = {**manifest['body'], 'verification': 'epsilon/not-calibrated'}
        case.send(case.keys[3], 'register_collaboration', {'manifest': {'id': identity(body), 'body': body}}, False)
        for name, value in [('plan', state['plan']), ('transactions', case.transcript), ('state', case.state),
                            ('selections', selections), ('resolved', [select_checkpoint(case.state, s) for s in selections]),
                            ('summary', summary(case.state)), ('implementation', {'sha256': implementation_digest()})]:
            write_json(directory/(name+'.json'), value)
        result = verify(directory); result['cross_branch_substitution_rejected'] = True
        write_json(directory/'verification.json', result)
        return result
    finally: case.doCleanups()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--verify', action='store_true'); args = p.parse_args()
    print(canonical_json(verify(args.output) if args.verify else generate(args.output)).decode())
