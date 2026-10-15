"""Signed recovery transitions and real HTTP faults; no GPU/training claims."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile
import threading
import unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import test_collaboration as previous
from dawl_cluster.state import advance
from dawl_cluster.training_protocol import candidate, round_jobs
from dawl_cluster.training_recovery import SCHEMA, report_payload
from dawl_cluster.collaboration import select_checkpoint
from dawl_training.recovery import ContributionFailure, verify_contribution, store_failure, current_failure
from dawl_training.network import Network
from dawl_platform.artifacts import Objects
from dawl_profiles.profiles import describe
from dawl_node.common import canonical_json


class RecoveryTests(unittest.TestCase):
    for helper in ('send', 'report', 'activate', 'join', 'bundle', 'add', 'claim', 'finish',
                   'admit', 'current', 'take', 'receipt', 'finish_round', 'vote', 'environment',
                   'manifest', 'accept', 'branch', 'start', 'complete_stage', 'selection'):
        locals()[helper] = getattr(previous.CollaborationTests, helper)
    pub = staticmethod(previous.CollaborationTests.pub)

    def setUp(self):
        previous.CollaborationTests.setUp(self)
        self.activation6 = {'version': 6, 'implementation_sha256': 'c'*64}
        self.send(self.keys[3], 'activate_training_recovery', self.activation6)
        self.admit(self.trainers[2], 'TRAINER')
        self.spare = Ed25519PrivateKey.generate(); self.admit(self.spare, 'VERIFIER')
        self.agreement = self.manifest(members=self.trainers+self.verifiers+[self.spare])
        self.main = self.branch(self.agreement)
        self.policy = {'round_timeout_blocks': 30, 'max_rejections': 3, 'supersedes': None}
        self.start(self.main, schema=SCHEMA, recovery=self.policy)

    def context(self):
        return {'stage': self.stage['id'], 'round': self.current()['round'], 'revision': self.current()['recovery_revision']}

    def failure(self, job_id=None, key=None, reason='replay_mismatch', ok=True, **changes):
        if job_id is None: job_id = round_jobs(self.state, self.current())[0][0]
        job = self.state['training_jobs'][job_id]
        payload = report_payload(self.current(), job_id, job, reason, 'f'*64) | changes
        return self.send(key or self.verifiers[0], 'report_training_failure', payload, ok)

    def reject(self, report, key=None, ok=True, **changes):
        return self.send(key or self.keys[3], 'reject_training_update', self.context() | {'report': report} | changes, ok)

    def deadline(self):
        target = self.current()['round_started_height'] + self.policy['round_timeout_blocks']
        while self.state['height'] < target: advance(self.state, self.state['height']+1)

    def cancel(self, reason='round_timeout', key=None, ok=True):
        return self.send(key or self.keys[3], 'cancel_training_stage', self.context() | {'reason': reason}, ok)

    def test_rejection_requeues_preserves_evidence_and_requires_fresh_full_quorum(self):
        old = self.finish_round(); self.vote(self.verifiers[0], old)
        job_id, job = round_jobs(self.state, self.current())[0]
        submitted = copy.deepcopy(job['contribution'])
        report = self.failure(job_id)['report']
        self.vote(self.verifiers[1], old, False)  # unresolved report blocks promotion
        self.reject(report)
        self.assertFalse(self.current()['votes'])
        self.assertEqual(self.current()['recovery_history'][0]['contribution'], submitted)
        self.assertEqual(len(self.current()['recovery_history'][0]['votes']), 1)
        self.assertEqual(self.current()['head'], self.main['id'])
        self.assertIsNone(self.take(self.trainers[0])['job'])  # rejected owner cannot immediately reclaim it
        lease = self.take(self.trainers[2]); self.assertEqual(lease['job'], job_id)
        self.send(self.trainers[2], 'result_training', self.receipt(lease))
        self.vote(self.verifiers[0], old, False)
        body = candidate(self.state, self.current(), old['adapter'])
        self.assertEqual(body['recovery_revision'], 1)
        self.vote(self.verifiers[0], body | {'recovery_revision': True}, False)
        self.vote(self.verifiers[0], body)
        self.assertEqual(self.current()['head'], self.main['id'])
        self.vote(self.verifiers[1], body)
        self.assertEqual(self.current()['status'], 'complete')
        selected = select_checkpoint(self.state, self.selection(self.main, self.current()['head']))
        self.assertEqual(selected['adapter'], body['adapter'])

    def test_stale_reports_duplicate_reports_and_unauthorized_decisions_are_atomic(self):
        self.finish_round()
        report = self.failure()['report']
        self.failure(ok=False)
        for key in (self.trainers[0], self.spare, self.keys[3]): self.failure(key=key, ok=False)
        self.failure(key=self.verifiers[1], contribution='0'*64, ok=False)
        self.failure(key=self.verifiers[1], revision=True, ok=False)
        self.failure(key=self.verifiers[1], evidence='../file', ok=False)
        self.reject(report, key=self.verifiers[0], ok=False)
        self.reject(report, revision=1, ok=False)
        self.reject(report)
        self.reject(report, ok=False)
        self.failure(round=1, job_id=round_jobs(self.state, self.current())[1][0], ok=False)

    def test_missing_verifier_cancel_replace_without_weakening_quorum(self):
        old_id = self.stage['id']; old_body = copy.deepcopy(self.stage['body'])
        body = self.finish_round(); self.vote(self.verifiers[0], body)
        self.cancel(ok=False)
        self.deadline(); self.cancel(key=self.trainers[0], ok=False); self.cancel()
        self.assertEqual(self.current()['body'], old_body)
        self.assertEqual(self.current()['head'], self.main['id'])
        self.vote(self.verifiers[1], body, False)
        self.verifiers = [self.verifiers[0], self.spare]
        self.start(self.main, schema=SCHEMA, recovery=self.policy | {'supersedes': old_id})
        self.assertEqual(self.current()['head'], self.main['id'])
        self.complete_stage()
        record = self.state['training_checkpoints'][self.current()['head']]
        self.assertEqual(set(record['receipts']), {self.pub(k) for k in self.verifiers})
        self.assertEqual(self.state['training_stages'][old_id]['status'], 'cancelled')

    def test_cancellation_fences_inflight_leases_and_preserves_previous_checkpoint(self):
        # Start a two-round stage by cancelling the unused one first.
        self.deadline(); self.cancel(); previous_id = self.stage['id']
        self.start(self.main, schema=SCHEMA, recovery=self.policy | {'supersedes': previous_id},
                   rounds=2, work=self.legacy['body']['work'])
        body = self.finish_round()
        for key in self.verifiers: self.vote(key, body)
        head = self.current()['head']; parent_adapter = self.current()['adapter']
        lease = self.take(self.trainers[0]); pending = self.receipt(lease)
        self.deadline(); self.cancel(); cancelled = self.stage['id']
        self.assertEqual(self.current()['head'], head)
        self.assertEqual(self.state['model_branches'][self.main['id']]['adapter'], parent_adapter)
        self.send(self.trainers[0], 'result_training', pending, False)
        self.send(self.trainers[0], 'renew_training', {k: lease[k] for k in ('stage', 'job', 'generation')}, False)
        self.start(self.main, schema=SCHEMA, recovery=self.policy | {'supersedes': cancelled})
        self.assertEqual(self.current()['head'], head)
        self.assertIsNotNone(self.take(self.trainers[0])['job'])
        select_checkpoint(self.state, self.selection(self.main, head))

    def test_disputed_round_can_be_cancelled_but_not_promoted_or_edited(self):
        body = self.finish_round(); self.vote(self.verifiers[0], body)
        self.vote(self.verifiers[1], body | {'adapter': {'sha256': '0'*64, 'bytes': 8}})
        self.assertEqual(self.current()['status'], 'disputed')
        report = self.failure()['report']; self.reject(report, ok=False)
        self.cancel('disputed')
        self.assertEqual(self.current()['head'], self.main['id'])
        self.assertEqual(len(self.current()['recovery_history'][0]['votes']), 2)

    def test_recovery_is_opt_in_and_has_fixed_bounds(self):
        self.send(self.keys[3], 'activate_training_recovery', self.activation6, False)
        self.deadline(); self.cancel()
        self.start(self.main)  # legacy schema 2 still cannot be cancelled by this extension
        payload = {'stage': self.stage['id'], 'round': 0, 'revision': 0, 'reason': 'round_timeout'}
        self.send(self.keys[3], 'cancel_training_stage', payload, False)
        self.complete_stage()
        for changes in ({'max_rejections': 0}, {'max_rejections': True}, {'round_timeout_blocks': 6001},
                        {'supersedes': self.stage['id']}):
            self.start(self.main, False, schema=SCHEMA, recovery=self.policy | changes)
        del self.state['training_recovery_protocol']
        self.start(self.main, False, schema=SCHEMA, recovery=self.policy)
        self.send(self.trainers[0], 'activate_training_recovery', self.activation6, False)
        self.send(self.keys[3], 'activate_training_recovery', self.activation6 | {'implementation_sha256': '0'*64}, False)

    def test_rejection_budget_does_not_reset_on_reassignment(self):
        # A manifest can deliberately allow only one discard before stage replacement.
        self.deadline(); self.cancel(); old_id = self.stage['id']
        self.start(self.main, schema=SCHEMA, recovery=self.policy | {'max_rejections': 1, 'supersedes': old_id})
        self.finish_round(); report = self.failure()['report']; self.reject(report)
        self.send(self.trainers[2], 'result_training', self.receipt(self.take(self.trainers[2])))
        report = self.failure()['report']; self.reject(report, ok=False)
        self.deadline(); self.cancel()

    def test_failure_evidence_has_content_identity_and_stale_worker_fence(self):
        self.finish_round(); job_id, job = round_jobs(self.state, self.current())[0]
        observed = copy.deepcopy(self.current())
        failure = ContributionFailure(job_id, copy.deepcopy(job), 'replay_mismatch', {'observed': describe(b'changed!')})
        self.assertTrue(current_failure(self.state, observed, failure))
        objects = Objects(self.root/'failure-evidence')
        payload = store_failure(objects, observed, failure)
        content = canonical_json(failure.evidence(observed))
        self.assertEqual(objects.read({'sha256': payload['evidence'], 'bytes': len(content)}), content)
        report = self.send(self.verifiers[0], 'report_training_failure', payload)['report']; self.reject(report)
        self.assertFalse(current_failure(self.state, observed, failure))

    def test_replacement_cannot_switch_to_an_unaccepted_verifier(self):
        stranger = Ed25519PrivateKey.generate(); self.admit(stranger, 'VERIFIER')
        self.deadline(); self.cancel(); old_id = self.stage['id']
        self.start(self.main, False, schema=SCHEMA, recovery=self.policy | {'supersedes': old_id},
                   verifiers=sorted([self.pub(self.verifiers[0]), self.pub(stranger)]))

    def test_round_deadline_restarts_only_after_verified_progress(self):
        self.deadline(); self.cancel(); old_id = self.stage['id']
        self.start(self.main, schema=SCHEMA, recovery=self.policy | {'supersedes': old_id},
                   rounds=2, work=self.legacy['body']['work'])
        started = self.current()['round_started_height']
        advance(self.state, self.state['height']+1)
        self.finish_round(); report = self.failure()['report']; self.reject(report)
        self.assertEqual(self.current()['round_started_height'], started)
        self.send(self.trainers[2], 'result_training', self.receipt(self.take(self.trainers[2])))
        body = candidate(self.state, self.current(), {'sha256': 'b'*64, 'bytes': 8})
        for key in self.verifiers: self.vote(key, body)
        self.assertEqual(self.current()['round_started_height'], self.state['height'])
        self.assertGreater(self.current()['round_started_height'], started)


class WorkerFaultTests(unittest.TestCase):
    def test_real_http_corruption_withholding_fallback_and_replay_mismatch(self):
        payload = b'expected'; expected = describe(payload)
        class Handler(BaseHTTPRequestHandler):
            mode = 'corrupt'
            def do_GET(self):
                if self.server.mode == 'withhold': self.send_error(404); return
                data = payload if self.server.mode == 'good' else b'corrupt!'
                self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            def log_message(self, *args): pass
        servers = []
        try:
            for mode in ('corrupt', 'withhold', 'good'):
                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.mode = mode
                threading.Thread(target=server.serve_forever, daemon=True).start(); servers.append(server)
            urls = ['http://127.0.0.1:'+str(s.server_port) for s in servers]
            with tempfile.TemporaryDirectory() as temporary:
                objects = Objects(Path(temporary)/'objects')
                network = Network(objects, urls[:2]); job = {'contribution': {'adapter': expected, 'transaction': '1'*64}}
                with self.assertRaises(ContributionFailure) as caught:
                    verify_contribution(network, '2'*64, job, lambda: self.fail('should not replay unavailable bytes'))
                self.assertEqual(caught.exception.reason, 'artifact_unavailable')
                self.assertFalse((objects.root/expected['sha256']).exists())
                network = Network(objects, urls)
                check = verify_contribution(network, '2'*64, job, lambda: payload)
                self.assertTrue(check['bit_exact']); self.assertEqual(network.bytes, len(payload))
                with self.assertRaises(ContributionFailure) as caught:
                    verify_contribution(network, '2'*64, job, lambda: b'wrong!!!')
                self.assertEqual(caught.exception.reason, 'replay_mismatch')
                def broken_runtime(): raise RuntimeError('execution environment failure')
                with self.assertRaises(RuntimeError): verify_contribution(network, '2'*64, job, broken_runtime)
        finally:
            for server in servers: server.shutdown(); server.server_close()


if __name__ == '__main__': unittest.main()
