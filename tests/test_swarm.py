"""Adversarial swarm transport, admission, replay and durable observation tests."""
import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import time
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, keygen, load_key, sha256, sign
from dawl_platform.artifacts import Objects
from dawl_platform.ledger import Ledger
from dawl_platform.auditor import verify_book
from dawl_swarm.protocol import (PROVIDER, AUDITOR, LEDGER_REPLICATOR, admit, announcement, validate_policy,
                                 verify_admission, verify_announcement)
from dawl_swarm.state import State
from dawl_swarm.transfer import PIECE, Puller, Unavailable, catalog
import test_platform


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.public = keygen(self.root / "key.pem", self.root / "public")
        self.key = load_key(self.root / "key.pem")
        self.ledger = Ledger(self.root / "ledger.sqlite", {"schema": "dawl.trust/1", "identities": {self.public: ["vector"]}})
        self.vector = self.ledger.create("vectors", {"chunks": 10}, [], self.key)
        self.policy = {"schema": "dawl.swarm-policy/1", "authority": self.public, "lineage_policy": self.ledger.policy,
                       "members": {self.public: {"name": "test", "roles": 7, "endpoint": "http://127.0.0.1:8900"}}}
        self.cert = admit(self.policy, self.public, self.key)
        self.state = State(self.root / "state.sqlite", self.policy, self.ledger)

    def receipt(self, outcome="pass", positions=None):
        return sign({"schema": "dawl.swarm-audit/1", "policy": validate_policy(self.policy),
                     "vectors_entry": self.vector["id"], "checkpoint": self.ledger.checkpoint(), "nonce": "a" * 64,
                     "positions": positions or [1, 5], "total_chunks": 10, "outcome": outcome,
                     "vector_commitment_match": {"pass": True, "invalid": False}.get(outcome),
                     "execution_time_ms": 0, "elapsed_time_ms": 2, "error": "", "rechecks": None,
                     "scope": "sampled-input-embedding/v1"}, self.key)

    def test_role_escalation_endpoint_rebinding_and_wrong_authority(self):
        self.assertEqual(verify_admission(self.cert, self.policy), self.public)
        for field, value in (("roles", 15), ("endpoint", "http://169.254.169.254:80")):
            bad = sign({**self.cert["body"], field: value}, self.key)
            with self.assertRaises(ValueError):
                verify_admission(bad, self.policy)
        other = keygen(self.root / "other.pem", self.root / "other.pub")
        bad = sign(self.cert["body"], load_key(self.root / "other.pem"))
        with self.assertRaises(ValueError):
            verify_admission(bad, self.policy)

    def test_announcements_expire_replay_and_equivocation_survive_restart(self):
        env = announcement(self.key, self.cert, self.policy, 2, self.ledger.checkpoint(), [])
        self.assertTrue(self.state.peer(env))
        self.assertFalse(self.state.peer(env))
        restored = State(self.state.path, self.policy, self.ledger)
        lower = announcement(self.key, self.cert, self.policy, 1, self.ledger.checkpoint(), [])
        with self.assertRaisesRegex(ValueError, "replayed"):
            restored.peer(lower)
        equivocal = sign({**env["body"], "inventory": ["b" * 64]}, self.key)
        with self.assertRaisesRegex(ValueError, "equivocal"):
            restored.peer(equivocal)
        with self.assertRaisesRegex(ValueError, "stale"):
            verify_announcement(env, self.policy, now=int(time.time()) + 180)

    def test_receipt_is_a_claim_and_does_not_enter_training_ledger(self):
        checkpoint = self.ledger.checkpoint()
        receipt = self.receipt()
        self.state.add(receipt)
        self.assertFalse(self.state.get(receipt["id"])["locally_executed"])
        self.assertEqual(self.ledger.checkpoint(), checkpoint)
        epoch = self.state.batch(self.key)
        self.assertEqual(epoch["body"]["receipts"], [receipt["id"]])
        self.assertIsNone(self.state.batch(self.key))
        with self.assertRaises(ValueError):
            self.state.add(sign({**epoch["body"], "previous": "f" * 64}, self.key))
        with self.assertRaises(ValueError):
            self.state.add(self.receipt(positions=[1, 1]))
        with self.assertRaises(ValueError):
            self.state.add(sign({**receipt["body"], "vector_commitment_match": False}, self.key))

    def test_challenge_survives_restart_and_unavailable_retries_same_sample(self):
        retained = {"positions": [1, 5], "nonce": "a" * 64}
        self.state.job("work", retained)
        self.state.finish("work", self.receipt("unavailable"))
        restored = State(self.state.path, self.policy, self.ledger)
        job = restored.job("work", {"positions": [2], "nonce": "b" * 64})
        self.assertEqual(job["challenge"], retained)
        self.assertIsNone(job["receipt"])
        self.assertGreater(job["retry_at"], time.time())


class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = ProtocolTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.objects = Objects(self.fixture.root / "objects")
        self.data = b"x" * PIECE + b"z" * 123
        self.expected = {"sha256": sha256(self.data), "bytes": len(self.data)}
        self.listing = {"artifact": self.expected, "piece_bytes": PIECE,
                        "pieces": [sha256(self.data[:PIECE]), sha256(self.data[PIECE:])]}
        self.calls, self.mode = [], "corrupt"

        def handler(request):
            host, route = request.url.host, request.url.path
            self.calls.append((host, route))
            if self.mode == "unavailable" or (self.mode == "interrupted" and route.endswith("/1")):
                return httpx.Response(503, stream=httpx.ByteStream(b"missing"))
            if "/catalog/" in route:
                listing = copy.deepcopy(self.listing)
                if self.mode == "dishonest-catalog" and host == "bad":
                    listing["pieces"][0] = sha256(b"q" * PIECE)
                return httpx.Response(200, stream=httpx.ByteStream(canonical_json(listing)))
            index = int(route.rsplit("/", 1)[1])
            payload = self.data[index * PIECE:(index + 1) * PIECE]
            if host == "bad" and self.mode == "corrupt":
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            if host == "bad" and self.mode == "dishonest-catalog" and index == 0:
                payload = b"q" * PIECE
            return httpx.Response(200, stream=httpx.ByteStream(payload))

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
        self.addAsyncCleanup(self.client.aclose)
        self.puller = Puller(self.client, self.objects, self.fixture.state, ["http://bad:80", "http://good:80"])

    async def test_corruption_fallback_and_concurrent_download_deduplication(self):
        await asyncio.gather(*(self.puller.fetch(self.expected, ["http://bad:80", "http://good:80"]) for _ in range(3)))
        self.assertEqual(self.objects.read(self.expected), self.data)
        counters = self.fixture.state.counters()
        self.assertEqual(counters["pieces_downloaded"], 2)
        self.assertEqual(counters["objects_downloaded"], 1)
        self.assertEqual(counters["object_cache_hits"], 2)
        self.assertEqual(counters["corrupt_pieces_rejected"], 2)

    async def test_dishonest_catalog_cannot_replace_whole_object_pin(self):
        self.mode = "dishonest-catalog"
        await self.puller.fetch(self.expected, ["http://bad:80", "http://good:80"])
        self.assertEqual(self.objects.read(self.expected), self.data)
        self.assertTrue(any(e["kind"] == "provider_failed" for e in self.fixture.state.events()))

    async def test_withholding_is_availability_not_transform_invalidity(self):
        self.mode = "unavailable"
        with self.assertRaises(Unavailable):
            await self.puller.fetch(self.expected, ["http://bad:80", "http://good:80"])
        self.assertFalse((self.objects.root / self.expected["sha256"]).exists())

    async def test_interrupted_transfer_resumes_and_rejects_unadmitted_requests(self):
        self.mode = "interrupted"
        with self.assertRaises(Unavailable):
            await self.puller.fetch(self.expected, ["http://good:80"])
        self.assertFalse((self.objects.root / self.expected["sha256"]).exists())
        self.assertTrue((self.puller.pieces / self.listing["pieces"][0]).exists())
        self.mode = "honest"
        restarted = Puller(self.client, self.objects, self.fixture.state, ["http://good:80"])
        await restarted.fetch(self.expected, ["http://good:80"])
        self.assertGreaterEqual(self.fixture.state.counters()["piece_cache_hits"], 1)
        with self.assertRaisesRegex(ValueError, "outside pinned"):
            await restarted.request("http://169.254.169.254:80", "/metadata")


class SampleReplayTests(unittest.TestCase):
    def setUp(self):
        self.f = test_platform.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_sampling_checks_selected_chunks_and_full_integrity(self):
        f = self.f
        report = verify_book(f.ledger, f.objects, None, f.vector, {}, positions=[0])
        self.assertEqual(report["checked_chunks"], 1)
        self.assertGreater(report["total_chunks"], 1)
        self.assertEqual(report["positions"], [0])
        for invalid in ([], [0, 0], [-1], [True], [999]):
            with self.assertRaises(ValueError):
                verify_book(f.ledger, f.objects, None, f.vector, {}, positions=invalid)

    def test_freeloader_can_sign_pass_but_independent_replay_rejects_bad_vector(self):
        f = self.f
        bad = copy.deepcopy(f.vector)
        values = f.objects.read_json(bad["body"]["payload"]["files"]["index"])
        values[0]["vectors_sha256"] = "0" * 64
        bad["body"]["payload"]["files"]["index"] = f.objects.json(values)
        bad = sign(bad["body"], load_key(f.root / "identities/vector/key.private.pem"))
        f.ledger.validate(bad)
        with self.assertRaisesRegex(ValueError, "vector mismatch"):
            verify_book(f.ledger, f.objects, None, bad, {}, positions=[0])
        # A sample which does not include the corruption can pass. Never claim full verification.
        self.assertTrue(verify_book(f.ledger, f.objects, None, bad, {}, positions=[1])["accepted"])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_announcements_are_bounded_and_cannot_submit_jobs_or_read_keys(self):
        from dawl_swarm.lab import prepare_lab
        from dawl_swarm.service import app
        from dawl_swarm.node import Node
        fixture = test_platform.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        lab_root = fixture.base / "swarm"
        prepare_lab(lab_root, fixture.root)
        provider = app(lab_root / "provider-a")
        provider.state.node.announce()
        auditor = Node(lab_root / "auditor")
        env = auditor.announce()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=provider), base_url="http://local") as client:
            self.assertEqual((await client.post("/swarm/announce", content=canonical_json(env))).status_code, 200)
            self.assertIn(auditor.public, provider.state.node.known)
            self.assertEqual((await client.post("/swarm/announce", content=b"x" * 65537)).status_code, 413)
            self.assertEqual((await client.post("/swarm/announce", content=b"{}")).status_code, 400)
            for route in ("/key.private.pem", "/fault.json", "/swarm/jobs"):
                self.assertEqual((await client.get(route)).status_code, 404)
            self.assertEqual((await client.post("/api/ledger", content=b"{}")).status_code, 405)
        # A dedicated auditor may consume ledger metadata, but does not claim to serve the replicator hat.
        audit_app = app(lab_root / "auditor")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=audit_app), base_url="http://local") as client:
            self.assertEqual((await client.get("/api/ledger")).status_code, 403)


if __name__ == "__main__":
    unittest.main()
