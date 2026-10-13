"""Real subprocess/HTTP boundaries. Run with permission to bind localhost ports."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from test_ingestion_nodes import make_fixture
from dawl_node.common import keygen, verify_signature
from dawl_node.audit import audit_peer
from dawl_node.transport import PeerClient, consume

ROOT = Path(__file__).resolve().parents[1]


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class HTTPNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.f = make_fixture(Path(cls.temporary.name))
        cls.token = secrets.token_urlsafe(32)
        cls.token_path = cls.f["root"] / "api.token"
        cls.token_path.write_text(cls.token, encoding="ascii")
        cls.validator_public = keygen(cls.f["root"] / "validator.private.pem", cls.f["root"] / "validator.key")
        cls.storage_url = cls.start([
            "serve-storage", "--store", str(cls.f["store"]), "--token-file", str(cls.token_path),
        ])
        cls.peer = PeerClient(cls.storage_url, cls.token)
        cls.validator_url = cls.start([
            "serve-validator", "--peer", cls.storage_url, "--peer-token-file", str(cls.token_path),
            "--token-file", str(cls.token_path), "--registry", str(cls.f["registry_path"]),
            "--registry-id", cls.f["registry"]["id"], "--tokenizer", str(cls.f["tokenizer_path"]),
            "--tokenizer-sha256", cls.f["core"].sha256, "--miner-public-key", str(cls.f["public_path"]),
            "--key", str(cls.f["root"] / "validator.private.pem"),
        ])

    @classmethod
    def start(cls, arguments):
        port = unused_port()
        log = (cls.f["root"] / f"service-{port}.log").open("wb")
        cls.addClassCleanup(log.close)
        process = subprocess.Popen([sys.executable, str(ROOT / "dawl_node.py"), *arguments, "--port", str(port)],
                                   stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def stop():
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        cls.addClassCleanup(stop)
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"service failed: {(cls.f['root'] / f'service-{port}.log').read_text()}")
            try:
                with urlopen(url + "/health", timeout=1) as response:
                    if response.status == 200:
                        return url
            except (OSError, URLError):
                time.sleep(0.05)
        raise RuntimeError("service did not become ready")

    def test_independent_validator_issues_a_signed_receipt(self):
        client = PeerClient(self.validator_url, self.token)
        receipt = json.loads(client.request("/v1/audits", {"snapshot_id": self.f["report"]["snapshot_id"], "samples": 3}))
        result = verify_signature(receipt, self.validator_public)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["checked_chunks"], 3)

    def test_consumer_replicates_and_verifies_all_shards(self):
        result = consume(self.peer, self.f["report"]["snapshot_id"], self.f["registry"], self.f["core"],
                         self.f["public"], self.f["root"] / "consumer")
        self.assertEqual(result["verified_tokens"], self.f["report"]["tokens"])

    def test_unauthorized_requests_and_non_public_files_are_rejected(self):
        snapshot = self.f["report"]["snapshot_id"]
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.storage_url + f"/v1/snapshots/{snapshot}/manifest", timeout=2)
        self.assertEqual(caught.exception.code, 401)
        with self.assertRaises(ValueError):
            self.peer.request(f"/v1/snapshots/{snapshot}/files/key.private.pem")

    def test_oversized_and_wrongly_typed_challenges_are_rejected(self):
        snapshot = self.f["report"]["snapshot_id"]
        request = Request(self.storage_url + f"/v1/snapshots/{snapshot}/challenge", data=b"x" * 20000,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 413)
        with self.assertRaises(ValueError):
            self.peer.challenge(snapshot, {"snapshot_id": snapshot, "nonce": "a" * 64, "positions": [True]})

    def test_role_routes_are_separate(self):
        with self.assertRaises(ValueError):
            self.peer.request("/v1/audits", {"snapshot_id": self.f["report"]["snapshot_id"], "samples": 1})
        validator = PeerClient(self.validator_url, self.token)
        with self.assertRaises(ValueError):
            validator.manifest(self.f["report"]["snapshot_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
