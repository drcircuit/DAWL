import asyncio
from contextlib import contextmanager
import copy
import time
import unittest

import httpx
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import test_evidence
from dawl_evidence.consumer import collect
from dawl_evidence.investigator import (assess_claim, investigate, verify_assessment,
    label_scores, result_label, aggregate, POLICY, POLICY_ID)
from dawl_evidence.assessment_service import app, discover
from dawl_evidence.evaluate import metrics, calibration_diagnostics, evaluate
from dawl_lab.mesh import DHT, validate
from dawl_node.common import sign, sha256, canonical_json, keygen, write_json


class FixtureEvaluator:
    descriptor = {'schema': 'test-only', 'policy_sha256': POLICY_ID}
    identity = sha256(canonical_json(descriptor))
    def score(self, premise, claim):
        return {'status': 'scored', 'tokens': 30,
                'scores': {'entailment': .96, 'neutral': .03, 'contradiction': .01}}


class InvestigatorTests(unittest.TestCase):
    def setUp(self):
        test_evidence.EvidenceTests.setUp(self)
        self.answer = 'Photosynthesis converts light energy into chemical energy.'
        def transport(request):
            result = self.api.request(request.method, request.url.path, content=request.content,
                                      headers={'Content-Type': 'application/json'})
            return httpx.Response(result.status_code, content=result.content)
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            self.collection = collect(self.answer, [{'start': 0, 'end': len(self.answer)}], 'a'*64,
                                      self.bp, [{'url': 'http://127.80.0.13:8914', 'signer': self.public}], self.key, client)
        self.evaluator = FixtureEvaluator()
        self.investigator_key = Ed25519PrivateKey.generate()
        self.investigator_public = self.investigator_key.public_key().public_bytes_raw().hex()
        self.fetch = lambda digest: (self.corpus/'texts'/digest).read_bytes()

    def receipt(self):
        return investigate(self.collection, self.public, self.bp, self.answer, self.fetch,
                           self.evaluator, self.investigator_key, 'b'*64)

    def check(self, receipt):
        return verify_assessment(receipt, self.investigator_public, self.collection, self.evaluator.identity, 'b'*64)

    def test_thresholds_abstention_conflict_and_bad_numerics(self):
        self.assertEqual(label_scores({'entailment': .89, 'neutral': .1, 'contradiction': .01}), 'insufficient_evidence')
        self.assertEqual(label_scores({'entailment': .01, 'neutral': .01, 'contradiction': .98}), 'contradicted')
        self.assertEqual(aggregate(['supported', 'contradicted']), 'conflicting')
        self.assertEqual(aggregate([]), 'insufficient_evidence')
        for scores in ({'entailment': float('nan'), 'neutral': .1, 'contradiction': .1},
                       {'entailment': True, 'neutral': 0, 'contradiction': 0},
                       {'entailment': .8, 'neutral': .8, 'contradiction': .8}):
            with self.assertRaises(ValueError): label_scores(scores)
        self.assertEqual(result_label({'status': 'context_limit', 'tokens': 513, 'scores': None}), 'insufficient_evidence')
        for raw in ({'status': 'unknown', 'tokens': 1, 'scores': None},
                    {'status': 'context_limit', 'tokens': 10, 'scores': None},
                    {'status': 'scored', 'tokens': 513, 'scores': self.evaluator.score('', '')['scores']}):
            with self.assertRaises(ValueError): result_label(raw)

    def test_signed_binding_and_policy_tampering(self):
        receipt = self.receipt()
        self.assertTrue(self.check(receipt)['verified'])
        self.assertIsNone(receipt['body']['claims'][0]['confidence'])
        self.assertEqual(receipt['body']['answer_sha256'], sha256(self.answer.encode()))
        mutations = [lambda b: b.update(nonce='c'*64),
                     lambda b: b.update(checkpoint_binding='verified'),
                     lambda b: b['claims'][0].update(confidence=.99),
                     lambda b: b['claims'][0]['judgments'][0].update(verdict='contradicted'),
                     lambda b: b['claims'][0]['judgments'][0].update(passage_sha256='f'*64),
                     lambda b: b['provenance_verification'].update(verified=False),
                     lambda b: b['evaluator'].update(device='cuda')]
        for change in mutations:
            body = copy.deepcopy(receipt['body']); change(body)
            with self.assertRaises(ValueError): self.check(sign(body, self.investigator_key))
        self.collection['body']['checkpoint'] = 'd'*64
        with self.assertRaises(ValueError): self.check(receipt)

    def test_sources_rechecked_before_semantic_scoring(self):
        digest = self.collection['body']['claims'][0]['candidates'][0]['source_sha256']
        (self.corpus/'texts'/digest).write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.receipt()

    def test_service_admission_freshness_replay_and_bounds(self):
        @contextmanager
        def fetches(): yield self.fetch
        service = TestClient(app(self.bp, self.evaluator, self.investigator_key, {self.public}, fetches))
        body = {'schema': 'hypertrix.investigation-request/1', 'collection': self.collection, 'answer': self.answer,
                'evaluator_sha256': self.evaluator.identity, 'policy_sha256': POLICY_ID, 'nonce': 'b'*64,
                'issued': int(time.time())}
        with service:
            self.assertEqual(service.get('/health').json()['name'], 'Investigator')
            self.assertEqual(service.post('/investigate', json=sign(body, self.investigator_key)).status_code, 403)
            wrong = dict(body, evaluator_sha256='c'*64)
            self.assertEqual(service.post('/investigate', json=sign(wrong, self.key)).status_code, 400)
            stale = dict(body, issued=int(time.time())-121)
            self.assertEqual(service.post('/investigate', json=sign(stale, self.key)).status_code, 400)
            result = service.post('/investigate', json=sign(body, self.key))
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(self.check(result.json())['verified'])
            self.assertEqual(service.post('/investigate', json=sign(body, self.key)).status_code, 409)
            self.assertEqual(service.post('/investigate', content=b'x'*(1024*1024+1)).status_code, 413)

    def test_discovery_pins_evaluator_and_admission(self):
        root = self.root/'investigator'
        keygen(root/'key.private.pem', root/'key.public.hex')
        write_json(root/'profile-capabilities.json', [self.pin])
        write_json(root/'assessment-capabilities.json', {'evaluator_sha256': self.evaluator.identity, 'policy_sha256': POLICY_ID})
        dht = DHT(root, self.network, 'Investigator', '127.80.0.15', ['EVIDENCE_ASSESSOR'], [])
        self.addCleanup(lambda: asyncio.run(dht.client.aclose()))
        dht.announce()
        body = validate(dht.own, self.network)
        self.assertEqual(body['schema'], 'hypertrix.lab-peer/4')
        self.assertEqual(body['services']['investigator'], 'http://127.80.0.15:8915')
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'records': [dht.own]}))) as client:
            args = (client, 'http://127.80.0.14:8913', self.network, self.pin)
            self.assertEqual(len(discover(*args, {body['signer']}, self.evaluator.identity)), 1)
            self.assertEqual(discover(*args, set(), self.evaluator.identity), [])
            self.assertEqual(discover(*args, {body['signer']}, 'c'*64), [])

    def test_metrics_empty_denominators_and_split_leakage(self):
        self.assertIsNone(metrics([])['citation_precision_against_labels'])
        self.assertIsNone(calibration_diagnostics([])['entailment_ece'])
        rows = [{'gold': 'entailment', 'predicted': 'supported'},
                {'gold': 'contradiction', 'predicted': 'supported'},
                {'gold': 'neutral', 'predicted': 'insufficient_evidence'}]
        self.assertEqual(metrics(rows)['citation_precision_against_labels'], .5)
        self.assertEqual(metrics(rows)['false_support_rate'], .5)
        group = {'id': 'same', 'split': 'development', 'claims': ['a','b','c'], 'passage': 'x', 'language': 'en'}
        fixture = {'schema': 'hypertrix.investigator-fixtures/1', 'label_order': ['entailment','contradiction','neutral'],
                   'groups': [group, dict(group, split='test')]}
        with self.assertRaises(ValueError): evaluate(fixture, self.evaluator)


if __name__ == '__main__':
    unittest.main()
