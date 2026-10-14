"""Signed state-machine exercises; synthetic adapters, no hardware/training claims."""
import copy
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import test_oracle_training as old
from dawl_cluster.collaboration import identity, EXACT, select_checkpoint
from pathlib import Path
from dawl_cluster.state import evaluate, initial, advance
from dawl_node.common import read_json, canonical_json


class CollaborationTests(unittest.TestCase):
    # Reuse only fixture helpers, not inherited legacy tests.
    send = old.OracleTrainingTests.send
    report = old.OracleTrainingTests.report
    activate = old.OracleTrainingTests.activate
    join = old.OracleTrainingTests.join
    bundle = old.OracleTrainingTests.bundle
    add = old.OracleTrainingTests.add
    claim = old.OracleTrainingTests.claim
    finish = old.OracleTrainingTests.finish
    pub = staticmethod(old.OracleTrainingTests.pub)
    admit = old.OracleTrainingTests.admit
    current = old.OracleTrainingTests.current
    take = old.OracleTrainingTests.take
    receipt = old.OracleTrainingTests.receipt
    finish_round = old.OracleTrainingTests.finish_round
    vote = old.OracleTrainingTests.vote

    def setUp(self):
        old.OracleTrainingTests.setUp(self)
        self.legacy = copy.deepcopy(self.stage)
        for _ in range(2):
            body = self.finish_round()
            for key in self.verifiers: self.vote(key, body)
        self.send(self.keys[3], 'activate_collaboration', {'version': 5, 'implementation_sha256': 'c'*64})
        self.env_a = self.environment('GPU A'); self.env_b = self.environment('GPU B')

    def environment(self, name):
        b = {'schema': 'hypertrix.execution-environment/1', 'implementation_sha256': 'c'*64,
             'runtime': {k: 'test-1' for k in ('torch', 'transformers', 'safetensors', 'tokenizers', 'numpy')},
             'python': '3.test', 'system': 'Synthetic', 'machine': 'test', 'torch_build_sha256': 'd'*64,
             'device': {'type': 'cuda', 'name': name, 'capability': 'test', 'driver': 'test'},
             'execution': {'dtype': 'float32', 'attention': 'eager', 'compiler': 'eager/no-compile',
                           'tf32': False, 'deterministic': True, 'threads': 2, 'cublas_workspace_config': ':4096:8'}}
        return {'id': identity(b), 'body': b}

    def manifest(self, mode='homogeneous', environments=None, members=None, accept=True):
        members = members or self.trainers[:2]+self.verifiers
        b = {'schema': 'hypertrix.collaboration-manifest/1', 'name': mode+' research', 'mode': mode,
             'profiles': [self.a['id']], 'members': sorted(self.pub(k) for k in members),
             'environments': sorted(environments or [self.env_a], key=lambda e: e['id']),
             'verification': EXACT, 'admission': 'explicit-members/1'}
        value = {'id': identity(b), 'body': b}
        self.send(self.keys[3], 'register_collaboration', {'manifest': value})
        if accept:
            for key in members: self.accept(key, value, self.env_a)
        return value

    def accept(self, key, manifest, environment, ok=True):
        return self.send(key, 'accept_collaboration', {'collaboration': manifest['id'], 'environment': environment['id']}, ok)

    def branch(self, manifest, environment=None, parent=None, name='main', signer=None, ok=True):
        initial = self.state['training_checkpoints'][parent]['body']['adapter'] if parent else self.legacy['body']['initial_adapter']
        b = {'schema': 'hypertrix.model-branch/1', 'name': name, 'collaboration': manifest['id'],
             'environment': (environment or self.env_a)['id'], 'profile_id': self.a['id'],
             'token_blueprint': self.legacy['body']['token_blueprint'], 'parent': parent,
             'parent_model': '5'*64, 'model_contract': '6'*64, 'initial_adapter': initial}
        value = {'id': identity(b), 'body': b}
        self.send(signer or self.keys[3], 'register_model_branch', {'branch': value}, ok)
        return value

    def start(self, branch, ok=True, **changes):
        current = self.state['model_branches'][branch['id']]
        b = {**self.legacy['body'], 'schema': 'hypertrix.oracle-training-stage/2', 'rounds': 1,
             'work': self.legacy['body']['work'][:2], 'initial_adapter': current['adapter'],
             'branch': branch['id'], 'parent_checkpoint': current['head'], 'model_contract': branch['body']['model_contract'],
             'verifiers': sorted(self.pub(k) for k in self.verifiers), **changes}
        value = {'id': identity(b), 'body': b}
        self.send(self.keys[3], 'register_training_stage', {'stage': value}, ok)
        if ok: self.stage = value
        return value

    def complete_stage(self, digest='b'*64):
        body = self.finish_round(); body['adapter']['sha256'] = digest
        for key in self.verifiers: self.vote(key, body)
        return self.current()['head']

    def selection(self, branch, checkpoint):
        return {k: branch['body'][k] for k in ('collaboration', 'environment', 'profile_id')} | {
            'branch': branch['id'], 'checkpoint': checkpoint, 'verification': EXACT}

    def test_homogeneous_continuation_exact_quorum_and_consumer_pins(self):
        manifest = self.manifest(); branch = self.branch(manifest); self.start(branch)
        body = self.finish_round(); self.vote(self.verifiers[0], body)
        self.assertEqual(self.state['model_branches'][branch['id']]['head'], branch['id'])
        self.vote(self.verifiers[1], body); first = self.current()['head']
        selected = select_checkpoint(self.state, self.selection(branch, first))
        self.assertEqual(selected['mode'], 'homogeneous')
        self.start(branch); self.assertEqual(self.current()['head'], first)
        second = self.complete_stage('e'*64)
        self.assertNotEqual(first, second)
        self.assertEqual(self.state['training_checkpoints'][second]['body']['parent'], first)
        self.assertEqual(select_checkpoint(self.state, self.selection(branch, first)), selected)

    def test_explicit_forks_can_diverge_and_leave_original_unchanged(self):
        main = self.branch(self.manifest()); self.start(main); parent = self.complete_stage()
        extra_trainers = [Ed25519PrivateKey.generate() for _ in range(2)]
        extra_verifiers = [Ed25519PrivateKey.generate() for _ in range(2)]
        for key in extra_trainers: self.admit(key, 'TRAINER')
        for key in extra_verifiers: self.admit(key, 'VERIFIER')
        members = self.trainers[:2]+self.verifiers+extra_trainers+extra_verifiers
        manifest = self.manifest('heterogeneous', [self.env_a, self.env_b], members, False)
        for key in self.trainers[:2]+self.verifiers: self.accept(key, manifest, self.env_a)
        for key in extra_trainers+extra_verifiers: self.accept(key, manifest, self.env_b)
        a = self.branch(manifest, parent=parent, name='A', signer=self.trainers[0])
        b = self.branch(manifest, self.env_b, parent, 'B', extra_trainers[0])
        self.start(a); head_a = self.complete_stage('a'*64)
        self.trainers, self.verifiers = extra_trainers, extra_verifiers
        self.start(b); head_b = self.complete_stage('e'*64)
        self.assertNotEqual(head_a, head_b)
        self.assertEqual(self.state['model_branches'][main['id']]['head'], parent)
        self.assertEqual(self.state['training_checkpoints'][head_a]['body']['parent'], parent)
        self.assertEqual(self.state['training_checkpoints'][head_b]['body']['parent'], parent)
        for branch, head in ((a, head_a), (b, head_b)):
            self.assertEqual(select_checkpoint(self.state, self.selection(branch, head))['mode'], 'heterogeneous')
        with self.assertRaises(ValueError): select_checkpoint(self.state, self.selection(a, head_b))

    def test_manifest_is_immutable_and_cannot_claim_tolerance(self):
        manifest = self.manifest()
        for change in ({'mode': 'unknown'}, {'verification': 'epsilon/1'}, {'environments': [self.env_a, self.env_b]},
                       {'admission': 'self-asserted/1'}, {'members': [self.pub(self.trainers[2])]}):
            body = {**manifest['body'], **change}
            self.send(self.keys[3], 'register_collaboration', {'manifest': {'id': identity(body), 'body': body}}, False)
        altered = copy.deepcopy(manifest); altered['body']['name'] = 'edited without changing ID'
        self.send(self.keys[3], 'register_collaboration', {'manifest': altered}, False)
        self.send(self.keys[3], 'register_collaboration', {'manifest': manifest}, False)

    def test_admission_requires_invitation_acceptance_and_matching_cohort(self):
        manifest = self.manifest(accept=False); branch = self.branch(manifest)
        self.start(branch, False)
        self.admit(self.trainers[2], 'TRAINER')
        self.accept(self.trainers[2], manifest, self.env_a, False)
        self.accept(self.trainers[0], manifest, self.env_b, False)
        for key in self.trainers[:2]+self.verifiers: self.accept(key, manifest, self.env_a)
        self.accept(self.trainers[0], manifest, self.env_a, False)
        self.start(branch)
        self.send(self.trainers[2], 'claim_training', {'stage': self.stage['id']}, False)

    def test_stage_cannot_bypass_manifest_or_overwrite_head(self):
        branch = self.branch(self.manifest())
        self.send(self.keys[3], 'register_training_stage', {'stage': self.legacy}, False)
        for change in ({'parent_checkpoint': '0'*64}, {'initial_adapter': {'sha256': '0'*64, 'bytes': 8}},
                       {'model_contract': '0'*64}, {'parent_model': '0'*64}):
            self.start(branch, False, **change)
        self.start(branch); self.start(branch, False)
        checkpoint = self.complete_stage()
        self.start(branch, False, parent_checkpoint=branch['id'])
        self.assertEqual(self.state['model_branches'][branch['id']]['head'], checkpoint)

    def test_fork_rejects_missing_or_incompatible_parent(self):
        branch = self.branch(self.manifest()); self.start(branch); parent = self.complete_stage()
        value = self.branch(self.manifest('heterogeneous'), parent=parent, name='valid')
        for change in ({'parent': '0'*64}, {'parent': [parent]}, {'profile_id': self.b['id']},
                       {'initial_adapter': {'sha256': '0'*64, 'bytes': 8}}, {'model_contract': '0'*64}):
            body = {**value['body'], **change}
            self.send(self.keys[3], 'register_model_branch', {'branch': {'id': identity(body), 'body': body}}, False)

    def test_consumer_does_not_accept_initial_state_or_policy_substitution(self):
        branch = self.branch(self.manifest())
        with self.assertRaises(ValueError): select_checkpoint(self.state, self.selection(branch, branch['id']))
        self.start(branch); head = self.complete_stage(); selection = self.selection(branch, head)
        for key in selection:
            with self.assertRaises((ValueError, KeyError)):
                select_checkpoint(self.state, {**selection, key: '0'*64})

    def test_conflicting_verifiers_freeze_only_their_branch(self):
        manifest = self.manifest(); a = self.branch(manifest, name='a'); b = self.branch(manifest, name='b')
        self.start(a); body = self.finish_round(); self.vote(self.verifiers[0], body)
        other = copy.deepcopy(body); other['adapter']['sha256'] = '0'*64; self.vote(self.verifiers[1], other)
        self.assertEqual(self.current()['status'], 'disputed')
        self.assertEqual(self.state['model_branches'][a['id']]['head'], a['id'])
        self.start(a, False); self.start(b); self.complete_stage()

    def test_worker_rejects_measured_environment_or_local_pin_mismatch(self):
        from dawl_training.environment import check_stage
        manifest = self.manifest(); branch = self.branch(manifest); self.start(branch)
        pins = {'collaboration_manifest': manifest['id'], 'execution_environment': self.env_a['id']}
        self.assertEqual(check_stage(self.state, self.current(), self.pub(self.trainers[0]), pins, self.env_a), self.env_a['body']['runtime'])
        for config, env in (({}, self.env_a), (pins, self.env_b)):
            with self.assertRaises(ValueError): check_stage(self.state, self.current(), self.pub(self.trainers[0]), config, env)

    def test_cross_cohort_lease_and_verifier_assignment_rejected(self):
        extra = Ed25519PrivateKey.generate(); self.admit(extra, 'TRAINER')
        members = self.trainers[:2]+self.verifiers+[extra]
        manifest = self.manifest('heterogeneous', [self.env_a, self.env_b], members, False)
        for key in self.trainers[:2]+self.verifiers: self.accept(key, manifest, self.env_a)
        self.accept(extra, manifest, self.env_b)
        a = self.branch(manifest); self.start(a)
        self.send(extra, 'claim_training', {'stage': self.stage['id']}, False)
        b = self.branch(manifest, self.env_b, name='wrong-verifiers')
        self.start(b, False)

    def test_activation_is_opt_in_once_and_waits_for_active_work(self):
        self.send(self.keys[3], 'activate_collaboration', {'version': 5, 'implementation_sha256': 'c'*64}, False)
        del self.state['collaboration_protocol']; del self.state['collaborations']; del self.state['model_branches']
        self.state['training_stages'][self.legacy['id']]['status'] = 'active'
        self.send(self.keys[3], 'activate_collaboration', {'version': 5, 'implementation_sha256': 'c'*64}, False)
        self.state['training_stages'][self.legacy['id']]['status'] = 'complete'
        self.send(self.trainers[0], 'activate_collaboration', {'version': 5, 'implementation_sha256': 'c'*64}, False)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_archived_protocol4_transitions_keep_identical_state_hash(self):
        # Compatibility regression, not a replacement for the archive's source-pinned certificate verifier.
        root = Path(__file__).resolve().parents[1]/'docs/results/oracle-training-run-003'
        state = initial(read_json(root/'genesis.json')['app_state'])
        history = read_json(root/'application-history.json')
        for height in range(1, history['height']+1):
            advance(state, height)
            for tx in history['blocks'].get(str(height), []):
                state, result = evaluate(state, tx)
                if result['code']: state['rejected_transactions'] += 1
        self.assertEqual(canonical_json(state), canonical_json(read_json(root/'training-certificate.json')['state']))
        self.assertNotIn('collaboration_protocol', state)


if __name__ == '__main__': unittest.main()
