"""Adversarial Oracle admission, assignment, fencing and checkpoint transitions."""
import copy
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import test_profile_protocol as profile_tests
from dawl_cluster.state import advance
from dawl_cluster.training_protocol import identity, round_jobs, candidate


class OracleTrainingTests(unittest.TestCase):
    send = profile_tests.ProtocolTests.send
    report = profile_tests.ProtocolTests.report
    activate = profile_tests.ProtocolTests.activate
    join = profile_tests.ProtocolTests.join
    bundle = profile_tests.ProtocolTests.bundle
    add = profile_tests.ProtocolTests.add
    claim = profile_tests.ProtocolTests.claim
    finish = profile_tests.ProtocolTests.finish

    def setUp(self):
        profile_tests.ProtocolTests.setUp(self)
        self.add(self.a)
        for who in (0, 1): self.finish(who, self.claim(who))
        release = self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']})
        self.activation4 = {'version': 4, 'implementation_sha256': '1'*64, 'runtime_manifest': '2'*64}
        self.send(self.keys[3], 'activate_training', self.activation4)
        self.trainers = [Ed25519PrivateKey.generate() for _ in range(3)]
        self.verifiers = [Ed25519PrivateKey.generate() for _ in range(2)]
        for key in self.trainers[:2]: self.admit(key, 'TRAINER')
        for key in self.verifiers: self.admit(key, 'VERIFIER')
        b = {'schema': 'hypertrix.oracle-training-stage/1', 'profile_id': self.a['id'],
             'token_blueprint': release['blueprint'], 'manifest': {'sha256': '3'*64, 'bytes': 100},
             'initial_adapter': {'sha256': '4'*64, 'bytes': 8}, 'parent_model': '5'*64,
             'rounds': 2, 'shards': 2, 'local_steps': 1, 'lease_blocks': 10,
             'verifiers': sorted(self.pub(k) for k in self.verifiers),
             'work': [{'round': i//2, 'shard': i % 2, 'start': i*2, 'sequences': 2,
                       'target_tokens': 256, 'data': identity({'windows': i})} for i in range(4)]}
        self.stage = {'id': identity(b), 'body': b}
        self.send(self.keys[3], 'register_training_stage', {'stage': self.stage})

    @staticmethod
    def pub(key): return key.public_key().public_bytes_raw().hex()

    def admit(self, key, role):
        return self.send(self.keys[3], 'admit_training_worker', {'public': self.pub(key), 'role': role, 'profiles': [self.a['id']]})

    def current(self): return self.state['training_stages'][self.stage['id']]

    def take(self, key): return self.send(key, 'claim_training', {'stage': self.stage['id']})

    def receipt(self, lease):
        return {**{k: lease[k] for k in ('stage', 'job', 'generation')}, 'parent': self.current()['head'],
                'adapter': {'sha256': 'a'*64, 'bytes': 8}}

    def finish_round(self):
        for key in self.trainers[:2]: self.send(key, 'result_training', self.receipt(self.take(key)))
        return candidate(self.state, self.current(), {'sha256': 'b'*64, 'bytes': 8})

    def vote(self, key, body, ok=True):
        return self.send(key, 'verify_training_round', {'stage': self.stage['id'], 'candidate': body}, ok)

    def test_training_admission_is_not_discovery_or_legacy_authority(self):
        self.send(self.keys[0], 'activate_training', self.activation4, False)
        self.send(self.keys[3], 'activate_training', self.activation4, False)
        self.send(self.trainers[2], 'claim_training', {'stage': self.stage['id']}, False)
        self.send(self.verifiers[0], 'claim_training', {'stage': self.stage['id']}, False)
        self.send(self.trainers[0], 'claim_profile', {'profile_ids': [self.a['id']]}, False)
        self.send(self.trainers[0], 'admit_training_worker', {'public': self.pub(self.trainers[2]), 'role': 'TRAINER', 'profiles': [self.a['id']]}, False)
        self.admit(self.trainers[2], 'TRAINER')
        self.assertIsNotNone(self.take(self.trainers[2])['job'])

    def test_training_stage_rejects_wrong_source_overlap_and_capability(self):
        for change in ({'token_blueprint': '0'*64}, {'profile_id': self.b['id']}, {'rounds': True},
                       {'verifiers': [self.pub(self.trainers[0]), self.pub(self.trainers[1])]}):
            b = {**self.stage['body'], **change}
            self.send(self.keys[3], 'register_training_stage', {'stage': {'id': identity(b), 'body': b}}, False)
        for field, value in [('start', 0), ('data', self.stage['body']['work'][0]['data']), ('round', True)]:
            b = copy.deepcopy(self.stage['body']); b['work'][1][field] = value
            self.send(self.keys[3], 'register_training_stage', {'stage': {'id': identity(b), 'body': b}}, False)

    def test_training_expiry_dynamic_replacement_and_late_result_fencing(self):
        abandoned = self.take(self.trainers[0]); other = self.take(self.trainers[1])
        self.assertEqual(self.take(self.trainers[0]), abandoned)
        self.send(self.trainers[1], 'result_training', self.receipt(other))
        self.assertIsNone(self.take(self.trainers[1])['job'])
        old = self.receipt(abandoned)
        while self.state['height'] <= abandoned['expires']: advance(self.state, self.state['height']+1)
        self.admit(self.trainers[2], 'TRAINER'); replacement = self.take(self.trainers[2])
        self.assertEqual(replacement['job'], abandoned['job']); self.assertNotEqual(replacement['generation'], abandoned['generation'])
        self.send(self.trainers[0], 'result_training', old, False)
        self.send(self.trainers[0], 'renew_training', {k: old[k] for k in ('stage', 'job', 'generation')}, False)
        self.send(self.trainers[2], 'result_training', {**self.receipt(replacement), 'generation': old['generation']}, False)
        self.send(self.trainers[2], 'result_training', self.receipt(replacement))
        body = candidate(self.state, self.current(), {'sha256': 'b'*64, 'bytes': 8})
        self.vote(self.verifiers[0], body); self.assertEqual(self.current()['round'], 0)
        self.vote(self.verifiers[1], body); self.assertEqual(self.current()['round'], 1)

    def test_training_quorum_chain_and_duplicate_wrong_parent_rejection(self):
        lease = self.take(self.trainers[0])
        self.send(self.trainers[0], 'result_training', {**self.receipt(lease), 'parent': '0'*64}, False)
        self.send(self.trainers[1], 'result_training', self.receipt(lease), False)
        self.send(self.trainers[0], 'result_training', self.receipt(lease))
        self.send(self.trainers[0], 'result_training', self.receipt(lease), False)
        self.send(self.trainers[1], 'result_training', self.receipt(self.take(self.trainers[1])))
        first = candidate(self.state, self.current(), {'sha256': 'b'*64, 'bytes': 8})
        self.vote(self.trainers[0], first, False)
        for change in ({'parent': '0'*64}, {'updates': first['updates'][::-1]}, {'verification': 'trust-me'}):
            self.vote(self.verifiers[0], {**first, **change}, False)
        self.vote(self.verifiers[0], first); self.vote(self.verifiers[0], first, False)
        self.vote(self.verifiers[1], first); parent = self.current()['head']
        self.vote(self.verifiers[0], first, False)
        second = self.finish_round(); self.assertEqual(second['parent'], parent)
        for key in self.verifiers: self.vote(key, second)
        self.assertEqual(self.current()['status'], 'complete')
        self.assertEqual(len(self.state['training_checkpoints']), 2)
        self.send(self.trainers[0], 'claim_training', {'stage': self.stage['id']}, False)

    def test_training_conflicting_reductions_freeze_without_promotion(self):
        body = self.finish_round(); self.vote(self.verifiers[0], body)
        self.vote(self.verifiers[1], {**body, 'adapter': {'sha256': 'c'*64, 'bytes': 8}})
        self.assertEqual(self.current()['status'], 'disputed'); self.assertEqual(self.current()['head'], self.stage['id'])
        self.assertFalse(self.state['training_checkpoints'])

    def test_training_renewal_and_cross_stage_lease_fencing(self):
        lease = self.take(self.trainers[0]); advance(self.state, self.state['height']+1)
        renewed = self.send(self.trainers[0], 'renew_training', {k: lease[k] for k in ('stage', 'job', 'generation')})
        self.assertEqual(renewed['generation'], lease['generation']); self.assertGreater(renewed['expires'], lease['expires'])
        b = {**self.stage['body'], 'parent_model': 'f'*64}; second = {'id': identity(b), 'body': b}
        self.send(self.keys[3], 'register_training_stage', {'stage': second})
        self.send(self.trainers[0], 'claim_training', {'stage': second['id']}, False)
        self.send(self.trainers[0], 'result_training', {**self.receipt(lease), 'stage': second['id']}, False)
