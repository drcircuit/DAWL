import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
import httpx

from dawl_evidence.consumer import collect, discover, verified_hits, verify_collection
from dawl_evidence.index import Blueprint, Index, build, file_hash
from dawl_evidence.provider import app
from dawl_lab.mesh import configure_policy, DHT, validate
from dawl_node.common import canonical_json, keygen, sha256, sign, verify_signature, write_json


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dataset = self.root / 'dataset'
        self.corpus = self.root / 'corpus'
        self.dataset.mkdir()
        (self.corpus / 'texts').mkdir(parents=True)
        self.documents = []
        for name, text, split in [
            ('alpha', 'Blåbær vokser i skogen. Plants use sunlight to convert carbon dioxide and water into sugars.', 'train'),
            ('beta', 'Photosynthesis converts light energy into chemical energy. Evidence can contradict a claim.', 'train'),
            ('heldout', 'An exclusively heldout zebra.', 'test'),
        ]:
            digest = sha256(text.encode('utf-8'))
            (self.corpus / 'texts' / digest).write_bytes(text.encode('utf-8'))
            self.documents.append({'id': name, 'text_sha256': digest, 'title': name,
                                   'split': split, 'group': name, 'origin': {'url': 'https://example.org/' + name},
                                   'rights': {'license': 'test-fixture'}})
        write_json(self.dataset / 'sources.json', {'documents': self.documents})
        write_json(self.dataset / 'blueprint.json', {'schema': 'hypertrix.lab-blueprint/1',
                                                   'sources_sha256': file_hash(self.dataset / 'sources.json')})
        self.pin = file_hash(self.dataset / 'blueprint.json')
        self.bp = Blueprint(self.dataset, self.pin)
        self.index_root = self.root / 'index'
        build(self.index_root, self.dataset, self.corpus, self.pin)
        self.index = Index(self.index_root, self.dataset, self.corpus, self.pin)
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes_raw().hex()
        self.policy = {'schema': 'hypertrix.lab-network/1', 'allowed_cidrs': ['127.80.0.0/24']}
        self.network = sha256(canonical_json(self.policy))
        configure_policy(self.policy, self.network)
        self.api = TestClient(app(self.index, self.key))
        self.api.__enter__()
        self.addCleanup(self.api.__exit__, None, None, None)

    def response(self, query='carbon dioxide'):
        request = {'query': query, 'blueprint': self.pin, 'nonce': 'a' * 64, 'limit': 3}
        response = self.api.post('/search', json=request)
        self.assertEqual(response.status_code, 200)
        return request, response.json()

    def check(self, request, response):
        return verified_hits(response, self.public, self.bp, request['query'], request['nonce'], 3,
                             lambda digest: self.api.get('/sources/' + digest).content)

    def test_unicode_sources_and_separate_evidence_meanings(self):
        request, response = self.response('Blåbær')
        hits = self.check(request, response)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['origin'], self.documents[0]['origin'])
        self.assertEqual(hits[0]['blueprint_membership'], 'verified')
        self.assertEqual(hits[0]['semantic_support'], 'not_assessed')
        self.assertEqual(hits[0]['training_exposure'], 'not_checked')
        self.assertIsNone(hits[0]['confidence'])
        self.assertEqual(self.index.manifest['documents'], 2)
        self.assertEqual(self.index.search('zebra'), [])
        self.assertEqual(self.api.get('/sources/' + self.documents[2]['text_sha256']).status_code, 404)

    def test_tamper_and_different_answer_query_rejected(self):
        request, response = self.response()
        for field, value in [('nonce', 'b' * 64), ('query_sha256', 'b' * 64), ('blueprint', 'b' * 64),
                             ('issued', int(time.time()) - 500)]:
            body = copy.deepcopy(response['body'])
            body[field] = value
            with self.assertRaises(ValueError):
                self.check(request, sign(body, self.key))
        body = copy.deepcopy(response['body'])
        body['hits'][0]['text'] = 'invented citation'
        body['hits'][0]['passage_sha256'] = sha256(b'invented citation')
        with self.assertRaises(ValueError):
            self.check(request, sign(body, self.key))
        body = copy.deepcopy(response['body'])
        body['hits'][0]['document'] = 'not-in-blueprint'
        with self.assertRaises(ValueError):
            self.check(request, sign(body, self.key))
        response['body']['hits'] = []  # unsigned mutation
        with self.assertRaises(ValueError):
            self.check(request, response)

    def test_corrupt_source_and_manifest_rejected(self):
        request, response = self.response()
        (self.corpus / 'texts' / self.documents[0]['text_sha256']).write_text('tampered', encoding='utf-8')
        self.assertEqual(self.api.get('/sources/' + self.documents[0]['text_sha256']).status_code, 409)
        with self.assertRaises(ValueError):
            self.check(request, response)
        (self.dataset / 'sources.json').write_text('{"documents":[]}', encoding='utf-8')
        with self.assertRaises(ValueError):
            Blueprint(self.dataset, self.pin)

    def test_query_language_and_bounds(self):
        self.assertIsInstance(self.index.search('" OR * NEAR() ; DROP TABLE passages --'), list)
        self.assertTrue(self.index.search('carbon'))
        self.assertEqual(self.api.post('/search', content=b'x' * 17000).status_code, 413)
        for query, limit in [('x' * 2001, 3), ('x', 100), ('x', True)]:
            self.assertEqual(self.api.post('/search', json={'query': query, 'limit': limit,
                             'blueprint': self.pin, 'nonce': 'a' * 64}).status_code, 400)

    def test_consumer_fallback_and_frozen_answer(self):
        def transport(request):
            if request.url.host == '127.80.0.99':
                return httpx.Response(503)
            result = self.api.request(request.method, request.url.path, content=request.content,
                                      headers={'Content-Type': 'application/json'})
            return httpx.Response(result.status_code, content=result.content)
        answer = 'Plants use sunlight to convert carbon dioxide and water into sugars.'
        peers = [{'url': 'http://127.80.0.99:8914', 'signer': 'b' * 64},
                 {'url': 'http://127.80.0.13:8914', 'signer': self.public}]
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            receipt = collect(answer, [{'start': 0, 'end': len(answer)}], 'c' * 64,
                              self.bp, peers, self.key, client)
        body = verify_signature(receipt, self.public)
        self.assertEqual(body['answer_sha256'], sha256(answer.encode('utf-8')))
        self.assertEqual(len(body['failures']), 1)
        self.assertTrue(body['claims'][0]['candidates'])
        self.assertEqual(body['claims'][0]['assessment'], 'pending')
        self.assertEqual(body['checkpoint_binding'], 'consumer_asserted')
        self.assertEqual(body['claims'][0]['text'], answer)
        fetch = lambda digest: (self.corpus / 'texts' / digest).read_bytes()
        checked = verify_collection(receipt, self.public, self.bp, answer, fetch)
        self.assertTrue(checked['verified'])
        with self.assertRaises(ValueError):
            verify_collection(receipt, self.public, self.bp, 'a different answer', fetch)
        changed = copy.deepcopy(body)
        changed['claims'][0]['candidates'][0]['semantic_support'] = 'supported'
        with self.assertRaises(ValueError):
            verify_collection(sign(changed, self.key), self.public, self.bp, answer, fetch)

    def test_network_and_claim_boundaries(self):
        for peers, spans in [
            ([{'url': 'http://169.254.169.254:8914', 'signer': self.public}], [{'start': 0, 'end': 1}]),
            ([{'url': 'http://127.80.0.13:8914', 'signer': self.public}], [{'start': 0, 'end': 999}]),
        ]:
            with self.assertRaises(ValueError):
                collect('claim', spans, 'c' * 64, self.bp, peers, self.key)

    def test_discovery_requires_admission_profile_and_valid_signature(self):
        root = self.root / 'peer'
        keygen(root / 'key.private.pem', root / 'key.public.hex')
        write_json(root / 'profile-capabilities.json', [self.pin])
        dht = DHT(root, self.network, 'evidence', '127.80.0.13', ['EVIDENCE_PROVIDER'], [])
        dht.announce()
        record = dht.own
        body = validate(record, self.network)
        self.assertEqual(body['schema'], 'hypertrix.lab-peer/3')
        self.assertEqual(body['services']['evidence'], 'http://127.80.0.13:8914')
        def response(request):
            return httpx.Response(200, json={'records': [record, record]})
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            self.assertEqual(discover(client, 'http://127.80.0.14:8913', self.network, self.pin, set()), [])
            self.assertEqual(len(discover(client, 'http://127.80.0.14:8913', self.network, self.pin, {body['signer']})), 1)
            self.assertEqual(discover(client, 'http://127.80.0.14:8913', self.network, 'c' * 64, {body['signer']}), [])
        with self.assertRaises(ValueError):
            validate(record, self.network, now=time.time() + 300)
        import asyncio
        asyncio.run(dht.client.aclose())


if __name__ == '__main__':
    unittest.main()
