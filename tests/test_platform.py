"""Adversarial lineage and actual source/token/vector replay boundaries."""
import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, Request

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, keygen, load_key, read_json, sha256, sign, write_json
from dawl_platform.ledger import Ledger
from dawl_platform.artifacts import Objects, PublicPeer, sync
from dawl_platform.vectors import EmbeddingTable
from dawl_platform.workflow import prepare, token_job, reconstruct, open_node
from dawl_platform.auditor import verify_book, audit, verify_public
from test_ingestion_nodes import make_fixture


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.public = keygen(self.root / "key.pem", self.root / "public")
        self.key = load_key(self.root / "key.pem")
        self.trust = {"schema": "dawl.trust/1", "identities": {self.public: ["source"]}}
        self.ledger = Ledger(self.root / "ledger.sqlite", self.trust)

    def test_replay_persistence_and_immutable_sql(self):
        a = self.ledger.create("resource", {"value": 1}, [], self.key)
        b = self.ledger.create("source", {"value": 2}, [a["id"]], self.key)
        self.assertFalse(self.ledger.add(a))
        reopened = Ledger(self.root / "ledger.sqlite", self.trust)
        self.assertEqual(reopened.verify_history(), self.ledger.checkpoint())
        self.assertEqual(reopened.get(b["id"])["body"]["previous"], a["id"])
        with closing(sqlite3.connect(reopened.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM entries")

    def test_tamper_untrusted_role_and_signature_rejected(self):
        a = self.ledger.create("resource", {"value": 1}, [], self.key)
        altered = copy.deepcopy(a)
        altered["body"]["payload"]["value"] = 7
        with self.assertRaises(ValueError):
            self.ledger.add(altered)
        with self.assertRaisesRegex(ValueError, "role"):
            self.ledger.create("audit", {}, [], self.key)
        unknown = self.root / "unknown"
        keygen(unknown, self.root / "unknown.pub")
        with self.assertRaises(ValueError):
            self.ledger.create("source", {}, [], load_key(unknown))

    def test_fork_gap_and_missing_parent_rejected(self):
        a = self.ledger.create("resource", {}, [], self.key)
        fork = sign({**a["body"], "payload": {"fork": True}}, self.key)
        with self.assertRaisesRegex(ValueError, "equivocation"):
            self.ledger.add(fork)
        with self.assertRaisesRegex(ValueError, "missing lineage"):
            self.ledger.create("source", {}, ["a" * 64], self.key)
        replica = Ledger(self.root / "replica.sqlite", self.trust)
        b = self.ledger.create("source", {}, [a["id"]], self.key)
        with self.assertRaisesRegex(ValueError, "gap"):
            replica.add(b)
        replica.add(a)
        replica.add(b)
        self.assertEqual(replica.checkpoint(), self.ledger.checkpoint())

    def test_policy_pin_and_offline_database_tamper(self):
        self.ledger.create("resource", {}, [], self.key)
        with self.assertRaisesRegex(ValueError, "policy changed"):
            Ledger(self.ledger.path, {**self.trust, "version": 2})
        with closing(sqlite3.connect(self.ledger.path)) as db, db:
            db.execute("DROP TRIGGER no_entry_update")
            envelope = next(self.ledger.entries())
            envelope["body"]["payload"] = {"bad": True}
            db.execute("UPDATE entries SET envelope=?", (canonical_json(envelope),))
        with self.assertRaises(ValueError):
            self.ledger.verify_history()

    def test_replication_rejects_truncated_checkpoint(self):
        entry = self.ledger.create("resource", {}, [], self.key)
        original = self.ledger
        class TruncatedPeer:
            def json(self, route):
                return original.checkpoint() if route == "/api/checkpoint" else {"after": 0, "next": 0, "entries": []}
        replica = Ledger(self.root / "replica.sqlite", self.trust)
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            sync(replica, TruncatedPeer())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        fixture_dir = self.base / "fixture"
        fixture_dir.mkdir()
        f = make_fixture(fixture_dir)
        self.f = f
        assets = self.base / "assets"
        assets.mkdir()
        (assets / "tokenizer.json").write_bytes(f["core"].payload)
        matrix = np.arange(f["core"].vocab_size * 4, dtype="<f4").reshape(-1, 4) / 7
        matrix_bytes = matrix.tobytes()
        self.descriptor = {"schema": "dawl.embedding-table/1", "dtype": "float32-le", "shape": list(matrix.shape),
            "sha256": sha256(matrix_bytes), "bytes": len(matrix_bytes), "tokenizer_sha256": f["core"].sha256,
            "contract": "token-input-embedding-lookup/v1; no positions, contextual layers, pooling or rounding"}
        (assets / "embedding.f32le").write_bytes(matrix_bytes)
        (assets / "upstream-model-card.md").write_text("Explicit test-only table", encoding="utf-8")
        write_json(assets / "embedding.json", self.descriptor)
        raw = self.base / "raw/11"
        raw.mkdir(parents=True)
        book_bytes = ("hello exact bytes. æøå 🌍\r\n" * 240).encode("utf-8")
        (raw / "pg11.txt").write_bytes(book_bytes)
        lock = {"books": [{"id": 11, "title": "Test source", "sha256": sha256(book_bytes), "bytes": len(book_bytes),
                           "source_url": "fixture:book"}]}
        write_json(self.base / "lock.json", lock)
        self.root = self.base / "node"
        prepare(self.root, raw.parent, self.base / "lock.json", assets, assets)
        self.ledger, self.objects = open_node(self.root)
        config = read_json(self.root / "config.json")
        result = token_job((str(self.root), config["jobs"][0], config))
        self.ledger.add(result["entry"])
        reconstruct(self.root, "cpu")
        self.vector = next(e for e in self.ledger.entries() if e["body"]["kind"] == "vectors")

    def test_complete_replay_covers_exact_bytes(self):
        result = verify_book(self.ledger, self.objects, None, self.vector, {})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["checked_chunks"], self.vector["body"]["payload"]["chunks"])
        self.assertEqual(result["dense_bytes_reconstructed"], result["tokens"] * 4 * 4)

    def test_signed_wrong_vectors_fail_even_with_valid_artifact_hash(self):
        wrong = copy.deepcopy(self.vector)
        index = self.objects.read_json(wrong["body"]["payload"]["files"]["index"])
        index[0]["vectors_sha256"] = "f" * 64
        wrong["body"]["payload"]["files"]["index"] = self.objects.json(index)
        wrong = sign(wrong["body"], load_key(self.root / "identities/vector/key.private.pem"))
        # Signature and object hash are both valid; recomputation still rejects the claim.
        self.ledger.validate(wrong)
        with self.assertRaisesRegex(ValueError, "vector mismatch"):
            verify_book(self.ledger, self.objects, None, wrong, {})

    def test_missing_vector_and_false_parent_fail(self):
        wrong = copy.deepcopy(self.vector)
        index = self.objects.read_json(wrong["body"]["payload"]["files"]["index"])
        wrong["body"]["payload"]["files"]["index"] = self.objects.json(index[:-1])
        with self.assertRaisesRegex(ValueError, "coverage"):
            verify_book(self.ledger, self.objects, None, wrong, {})
        wrong = copy.deepcopy(self.vector)
        wrong["body"]["parents"] = []
        with self.assertRaisesRegex(ValueError, "lineage parent"):
            verify_book(self.ledger, self.objects, None, wrong, {})

    def test_object_tamper_and_token_range_are_rejected(self):
        descriptor = self.vector["body"]["payload"]["files"]["index"]
        (self.objects.root / descriptor["sha256"]).write_bytes(b"tamper")
        with self.assertRaisesRegex(ValueError, "hash or size"):
            verify_book(self.ledger, self.objects, None, self.vector, {})
        table = EmbeddingTable(self.base / "assets/embedding.f32le", self.descriptor)
        for ids in ([-1], [self.f["core"].vocab_size], [1.5], []):
            with self.assertRaises(ValueError):
                table.payload(ids)
        with self.assertRaisesRegex(ValueError, "contract"):
            EmbeddingTable(self.base / "assets/embedding.f32le", {**self.descriptor, "contract": "different"})

    def test_public_http_replication_and_audit_without_producer_keys(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        repo = Path(__file__).resolve().parents[1]
        process = subprocess.Popen([sys.executable, str(repo / "dawl_platform.py"), "serve", "--root", str(self.root),
                                    "--port", str(port)], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.wait(timeout=10))
        self.addCleanup(process.terminate)
        peer = PublicPeer(f"http://127.0.0.1:{port}")
        for _ in range(100):
            try:
                peer.json("/health")
                break
            except (URLError, OSError):
                time.sleep(0.05)
        else:
            self.fail("HTTP peer failed to start")
        self.assertIn(b"Follow the data", peer.request("/"))
        self.assertEqual(peer.json("/api/checkpoint"), self.ledger.checkpoint())
        for route in ("/identities/source/key.private.pem", "/api/objects/not-a-digest", "/config.json"):
            with self.assertRaises(HTTPError):
                peer.request(route)
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(peer.url + "/api/ledger", data=b"{}"), timeout=5)
        self.assertEqual(caught.exception.code, 405)
        replica = self.base / "replica"
        replica.mkdir()
        write_json(replica / "trust.json", self.ledger.trust)
        replica_ledger, _ = open_node(replica)
        sync(replica_ledger, peer)
        self.assertEqual(replica_ledger.verify_history(), self.ledger.checkpoint())
        result = audit(replica, peer.url, key_path=self.root / "identities/auditor/key.private.pem")
        self.assertTrue(result["accepted"])
        self.assertEqual(len(result["receipts"]), 1)
        # Replay is idempotent and no producer identity has been copied into the replica.
        repeated = audit(replica, peer.url, key_path=self.root / "identities/auditor/key.private.pem")
        self.assertEqual(repeated["reused"], 1)
        self.assertEqual(repeated["receipts"], [])
        self.assertFalse((replica / "identities").exists())
        public_replay = verify_public(replica, peer.url)
        self.assertTrue(public_replay["accepted"])
        self.assertFalse(public_replay["private_keys_used"])


if __name__ == "__main__":
    unittest.main()
