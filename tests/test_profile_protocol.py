"""Protocol-3 admission, isolation, lease fencing, HTTP replay and durable recovery."""
import asyncio
import base64
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import httpx

import test_contributions as contributions_tests
from test_profiles import fixture
from dawl_node.common import canonical_json, write_json, sign, sha256
from dawl_cluster.state import evaluate, advance, summary
from dawl_cluster.application import Application
from dawl_cluster.profile_protocol import done
from dawl_cluster.profile_worker import replay
from dawl_profiles.workspace import Workspace
from dawl_profiles.bundles import export_bundle, import_bundle, export_index
from dawl_swarm.transfer import catalog, PIECE


class ProtocolTests(unittest.TestCase):
    send = contributions_tests.ContributionsTests.send
    report = contributions_tests.ContributionsTests.report
    activate = contributions_tests.ContributionsTests.activate
    join = contributions_tests.ContributionsTests.join

    def setUp(self):
        contributions_tests.ContributionsTests.setUp(self); self.join()
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.ws = Workspace(self.root/'provider')
        self.a = fixture(self.ws, 'raw'); self.b = fixture(self.ws, 'normalized', True, 7)
        self.p3 = dict(version=3, implementation_sha256='d'*64, runtime_manifest='e'*64)
        self.send(self.keys[3], 'activate_profiles', self.p3)
        for profile in (self.a, self.b): self.send(self.keys[3], 'register_profile', {'profile': profile})

    def bundle(self, profile, book_id=35, suffix=''):
        public = self.new.public_key().public_bytes_raw().hex()
        # Separate signed contributor snapshots can share the same profile namespace.
        trust = {'schema': 'dawl.trust/1', 'profile_id': profile['id'], 'snapshot': suffix,
                 'identities': {public: ['source', 'tokenizer']}}
        ws = self.ws if not suffix else Workspace(self.root/suffix)
        if suffix:
            for desc in (profile['body']['tokenizer']['artifact'], profile['body']['model']['config']):
                ws.objects.put(self.ws.objects.read(desc))
        ledger, tok = ws.open_profile(profile, trust)
        ws.ingest_book(ledger, tok, book_id, ('hello cafe\u0301 \r\n'*400).encode(), self.new)
        blueprint = ws.publish(ledger, tok, self.new)
        return export_bundle(ws, ledger, tok, blueprint, self.new)

    def add(self, profile, **kwargs):
        proposal = self.bundle(profile, **kwargs)
        result = self.send(self.new, 'contribute_profile', proposal)
        return proposal, result['contribution']

    def claim(self, who=0, profile=None):
        return self.send(self.keys[who], 'claim_profile', {'profile_ids': [profile or self.a['id']]})

    def finish(self, who, lease, outcome='pass'):
        spec = self.state['profile_jobs'][lease['job']]['spec']
        report = dict(outcome=outcome, checked_chunks=spec['frags'], tokens_checked=spec['tokens'],
                      implementation_sha256='d'*64, execution_ms=1, error='' if outcome == 'pass' else 'test failure')
        payload = {k: lease[k] for k in ('profile_id', 'job', 'generation')}
        return self.send(self.keys[who], 'result_profile', {**payload, 'report': report})

    def test_activation_and_registration_authority_atomicity_and_bounds(self):
        self.send(self.keys[3], 'activate_profiles', self.p3, False)
        self.send(self.keys[0], 'register_profile', {'profile': fixture(self.ws, 'third')}, False)
        self.send(self.keys[3], 'register_profile', {'profile': self.a}, False)
        self.send(self.new, 'claim_profile', {'profile_ids': [self.a['id']]}, False)
        self.send(self.keys[0], 'claim_profile', {'profile_ids': ['0'*64]}, False)
        self.send(self.keys[0], 'claim_profile', {'profile_ids': []}, False)
        bad = copy.deepcopy(self.b); bad['body']['label'] = 'relabelled'
        self.send(self.keys[3], 'register_profile', {'profile': bad}, False)
        self.send(self.new, 'contribute', self.proposal, False)
        self.assertEqual(self.old, self.state['dataset_release'])

    def test_activation_requires_completed_v2_without_outstanding_leases(self):
        original = copy.deepcopy(self.state)
        for field in ('profile_protocol','profiles','profile_contributions','profile_jobs','profile_heads','profile_releases','profile_weights'):
            self.state.pop(field)
        v2 = copy.deepcopy(self.state)
        job = next(iter(self.state['jobs'].values()))
        job['leases'][self.public[0]] = {'generation':'a'*64,'expires':99}
        self.send(self.keys[3], 'activate_profiles', self.p3, False)
        self.state = copy.deepcopy(v2); next(iter(self.state['jobs'].values()))['attestations'] = {}
        self.send(self.keys[3], 'activate_profiles', self.p3, False)
        self.state = copy.deepcopy(v2); self.state.pop('protocol_upgrade')
        self.send(self.keys[3], 'activate_profiles', self.p3, False)
        self.state = original

    def test_weight_layouts_are_profile_bound_and_do_not_claim_verified_training(self):
        manifest = {'schema':'hypertrix.named-dense-weights/1','profile_id':self.a['id'], 'tensors':{
            'model.embed_tokens.weight':{'shape':[264,4],'dtype':'float32-le','chunks':[{'sha256':'a'*64,'bytes':4224}]}}}
        payload = {'profile_id':self.a['id'],'manifest':manifest}
        self.send(self.keys[0], 'register_profile_weights', payload, False)
        self.send(self.keys[3], 'register_profile_weights', {**payload,'profile_id':self.b['id']}, False)
        result = self.send(self.keys[3], 'register_profile_weights', payload)
        record = self.state['profile_weights'][result['weight_manifest']]
        self.assertFalse(record['artifact_bytes_verified']); self.assertFalse(record['training_history_verified'])

    def test_same_book_coexists_and_unfinished_profile_cannot_block_another(self):
        self.add(self.a); self.add(self.b)
        self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']}, False)
        for who in (0, 1): self.finish(who, self.claim(who))
        self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']})
        self.send(self.keys[3], 'publish_profile', {'profile_id': self.b['id']}, False)
        self.assertIsNone(self.state['profile_heads'][self.b['id']])
        self.assertEqual(self.old, self.state['dataset_release'])
        self.assertEqual(summary(self.state)['profiles'][self.a['id']]['complete'], 1)
        self.assertIsNone(self.claim()['job'])

    def test_wrong_namespace_recipe_count_owner_and_expired_generation_rejected(self):
        self.add(self.a); self.add(self.b); lease = self.claim()
        p = {k: lease[k] for k in ('profile_id', 'job', 'generation')}
        self.send(self.keys[1], 'renew_profile', p, False)
        self.send(self.keys[0], 'renew_profile', {**p, 'profile_id': self.b['id']}, False)
        spec = self.state['profile_jobs'][lease['job']]['spec']
        report = dict(outcome='pass', checked_chunks=spec['frags'], tokens_checked=spec['tokens'],
                      implementation_sha256='d'*64, execution_ms=2, error='')
        for field, value in [('tokens_checked', 0), ('implementation_sha256', '0'*64), ('checked_chunks', True)]:
            self.send(self.keys[0], 'result_profile', {**p, 'report': {**report, field: value}}, False)
        for _ in range(11): advance(self.state, self.state['height']+1)
        self.send(self.keys[0], 'result_profile', {**p, 'report': report}, False)
        self.assertEqual(self.state['expired_leases'], 1)

    def test_release_extends_only_its_own_profile_head_and_is_idempotent(self):
        proposal, _ = self.add(self.a)
        self.send(self.new, 'contribute_profile', proposal, False)
        for who in (0, 1): self.finish(who, self.claim(who))
        first = self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']})
        self.assertEqual(first, self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']}))
        self.add(self.a, book_id=36, suffix='next')
        for who in (0, 1): self.finish(who, self.claim(who))
        self.send(self.keys[3], 'publish_profile', {'profile_id': self.a['id']})
        head = self.state['profile_heads'][self.a['id']]
        self.assertEqual(head['body']['parent_blueprint'], first['blueprint'])
        self.assertEqual(len(head['body']['books']), 2)
        self.assertEqual(len(self.state['profile_releases'][self.a['id']]), 2)

    def test_dispute_blocks_only_target_profile_and_unavailability_retries(self):
        self.add(self.a); self.add(self.b)
        self.finish(0, self.claim(), 'unavailable')
        self.finish(0, self.claim(), 'invalid')
        self.assertIsNone(self.claim(1)['job'])
        self.assertIsNotNone(self.claim(1, self.b['id'])['job'])

    def test_determinism_and_abci_commit_restart_preserve_profile_heads(self):
        self.add(self.a)
        before = copy.deepcopy(self.state)
        body = dict(schema='dawl.cluster-tx/1', chain_id=before['chain_id'], plan_sha256=before['plan_sha256'],
                    sequence=before['actors'][self.public[0]]['sequence']+1, valid_until=60,
                    kind='claim_profile', payload={'profile_ids': sorted([self.a['id'], self.b['id']])})
        tx = sign(body, self.keys[0]); reordered = copy.deepcopy(before)
        reordered['profiles'] = dict(reversed(list(reordered['profiles'].items())))
        self.assertEqual(canonical_json(evaluate(before, tx)[0]), canonical_json(evaluate(reordered, tx)[0]))
        write_json(self.root/'plan.json', before['plan']); app = Application(self.root)
        app.state = before
        result = app.finalize(before['height']+1, [base64.b64encode(canonical_json(tx)).decode()]); app.commit()
        self.assertEqual(Application(self.root).info()['hash'], result['hash'])

    def test_signed_bundle_substitution_cannot_cross_profile_or_catalog(self):
        proposal, batchid = self.add(self.a); batch = self.state['profile_contributions'][batchid]
        envelope = self.ws.objects.read_json(proposal['bundle'])
        with self.assertRaises(ValueError): import_bundle(self.root/'bad', envelope, batch, self.b)
        altered = copy.deepcopy(envelope['body']); altered['catalog']['books'][0]['tokens'] += 1
        with self.assertRaises(ValueError): import_bundle(self.root/'bad', sign(altered, self.new), batch, self.a)

    def test_real_http_pull_and_independent_retokenization(self):
        proposal, _ = self.add(self.a); lease = self.claim(); index = export_index(self.ws, [proposal]); ws = self.ws
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                parts = self.path.split('/'); digest = parts[3]
                if digest not in index: self.send_error(404); return
                if parts[2] == 'catalog': data = canonical_json(catalog(ws.objects.root/digest, index[digest]))
                else:
                    pos = int(parts[4]); data = ws.objects.read(index[digest])[pos*PIECE:(pos+1)*PIECE]
                self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        class Metrics:
            def increment(self, *args): pass
            def event(self, *args, **kwargs): pass
        async def check():
            async with httpx.AsyncClient(trust_env=False) as client:
                return await replay(self.root/'worker', self.state, lease['job'], [f'http://127.0.0.1:{server.server_port}'], client, Metrics())
        try:
            report = asyncio.run(check()); self.assertEqual(report['tokens'], proposal['catalog']['books'][0]['tokens'])
        finally: server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__': unittest.main()
