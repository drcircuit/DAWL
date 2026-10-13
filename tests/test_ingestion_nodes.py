"""Persistent ingestion and adversarial role-boundary tests; no network or training."""

import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokenizers import Tokenizer, models, pre_tokenizers, decoders
from data_ingestion.protocol import MerkleTree
from dawl_node.audit import make_challenge, verify_response
from dawl_node.common import (canonical_json, keygen, load_key, load_public, read_json,
                              sha256, sign, verify_signature, write_json)
from dawl_node.source import build_registry, chunks, load_registry, register
from dawl_node.store import mine, StoredSnapshot, validate_manifest, verify_snapshot
from dawl_node.tokenizer import PinnedTokenizer
from dawl_node.transport import PeerClient


def make_fixture(root: Path):
    # An explicit ByteLevel BPE vocabulary, without fitting or training a tokenizer.
    alphabet = sorted(pre_tokenizers.ByteLevel.alphabet())
    vocabulary = {token: index for index, token in enumerate(alphabet)}
    vocabulary["he"] = len(vocabulary)
    tokenizer = Tokenizer(models.BPE(vocabulary, [("h", "e")]))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer_path = root / "tokenizer.json"
    tokenizer.save(str(tokenizer_path))
    core = PinnedTokenizer(tokenizer_path, sha256(tokenizer_path.read_bytes()))
    docs = [
        {"id": "one", "text": "hello world. \u00e5\u00e6\u00f8 \U0001f30d\r\n" * 40, "source_uri": "fixture:one", "license": "CC0-1.0"},
        {"id": "two", "text": "A separate source with context.\n" * 30, "source_uri": "fixture:two", "license": "CC0-1.0"},
    ]
    docs.append({**docs[0], "id": "alias", "source_uri": "fixture:alias"})
    source = root / "input.jsonl"
    source.write_bytes(b"\n".join(canonical_json(doc) for doc in docs))
    registry_path = root / "source.json"
    register(source, registry_path, 256)
    registry = load_registry(registry_path)
    key_path, public_path = root / "key.private.pem", root / "public.key"
    public = keygen(key_path, public_path)
    store = root / "store"
    report = mine(source, registry_path, core, key_path, store)
    return {"root": root, "core": core, "tokenizer_path": tokenizer_path, "source": source,
            "registry_path": registry_path, "registry": registry, "key_path": key_path,
            "public_path": public_path, "public": public, "store": store, "report": report,
            "snapshot": StoredSnapshot(store, report["snapshot_id"])}


class IngestionNodeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = make_fixture(Path(self.temporary.name))

    def challenge(self, count=2):
        f = self.fixture
        challenge = make_challenge(f["snapshot"].envelope, f["registry"], f["core"], f["public"], count)
        response = f["snapshot"].respond(challenge["nonce"], challenge["positions"])
        return challenge, response

    def verify(self, challenge, response):
        f = self.fixture
        return verify_response(f["snapshot"].envelope, f["registry"], f["core"], f["public"], challenge, response)

    def test_persist_reload_and_full_integrity_verification(self):
        f = self.fixture
        reloaded = StoredSnapshot(f["store"], f["report"]["snapshot_id"])
        result = verify_snapshot(reloaded.directory, f["registry"], f["core"], f["public"])
        self.assertEqual(result["verified_tokens"], f["report"]["tokens"])
        self.assertEqual(result["verified_chunks"], f["report"]["chunks"])

    def test_no_empty_or_dense_embedding_artifacts(self):
        f = self.fixture
        files = {path.suffix for path in f["snapshot"].directory.iterdir()}
        self.assertEqual(files, {".json", ".u32le"})
        self.assertGreater(f["report"]["tokens"], 0)

    def test_exact_duplicate_documents_keep_alias_provenance(self):
        body = self.fixture["registry"]["body"]
        self.assertEqual(len(body["documents"]), 3)
        self.assertEqual(body["documents"][2]["canonical_document"], "one")
        self.assertEqual({record["document_id"] for record in body["records"]}, {"one", "two"})

    def test_utf8_chunks_reassemble_without_loss(self):
        raw = ("a" * 254 + "\U0001f30d\u00e5\r\n") .encode("utf-8") * 20
        parts = list(chunks(raw, 256))
        self.assertEqual(b"".join(payload for _, payload in parts), raw)
        for offset, payload in parts:
            self.assertEqual(raw[offset:offset + len(payload)], payload)
            self.assertLessEqual(len(payload), 256)
            payload.decode("utf-8", errors="strict")

    def test_lossless_real_bytelevel_encoding_and_bpe_merge(self):
        core = self.fixture["core"]
        for text in ("hello", "  tabs\tand\r\nlines", "\u00e6\U0001f30d e\u0301", "<|endoftext|>"):
            ids = core.encode(text)
            self.assertEqual(core.core.decode(ids, skip_special_tokens=False), text)
        self.assertLess(len(core.encode("hello")), len(b"hello"))

    def test_tokenizer_pin_and_runtime_descriptor_are_enforced(self):
        f = self.fixture
        with self.assertRaises(ValueError):
            PinnedTokenizer(f["tokenizer_path"], "0" * 64)
        with self.assertRaises(ValueError):
            f["core"].validate_descriptor({**f["core"].descriptor, "version": "different"})

    def test_stochastic_or_lossy_tokenizers_rejected(self):
        f = self.fixture
        for section, key, value in (("model", "dropout", 0.1), ("pre_tokenizer", "add_prefix_space", True)):
            definition = read_json(f["tokenizer_path"])
            definition[section][key] = value
            path = f["root"] / "bad-tokenizer.json"
            path.write_bytes(canonical_json(definition))
            with self.assertRaises(ValueError):
                PinnedTokenizer(path, sha256(path.read_bytes()))

    def test_idempotent_ingestion_has_stable_snapshot_id(self):
        f = self.fixture
        second = mine(f["source"], f["registry_path"], f["core"], f["key_path"], f["store"])
        self.assertEqual(second["snapshot_id"], f["report"]["snapshot_id"])
        self.assertEqual(len(list((f["store"] / "snapshots").iterdir())), 1)
        self.assertFalse(list((f["store"] / ".staging").iterdir()))

    def test_source_change_cannot_publish_under_existing_registry(self):
        f = self.fixture
        text = f["source"].read_text("utf-8").replace("hello", "jello")
        f["source"].write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
            mine(f["source"], f["registry_path"], f["core"], f["key_path"], f["store"])
        self.assertEqual(len(list((f["store"] / "snapshots").iterdir())), 1)

    def test_registered_hash_cannot_be_replaced(self):
        f = self.fixture
        with self.assertRaises(ValueError):
            load_registry(f["registry_path"], "0" * 64)
        registry = copy.deepcopy(f["registry"])
        registry["body"]["records"][0]["sha256"] = "0" * 64
        f["registry_path"].write_bytes(canonical_json(registry))
        with self.assertRaises(ValueError):
            load_registry(f["registry_path"])

    def test_bad_metadata_and_duplicate_document_ids_rejected(self):
        f = self.fixture
        cases = [b"", b'{"id":"a","text":"x"}', b'{"id":1,"text":"x","source_uri":"s","license":"l"}']
        doc = {"id": "same", "text": "hello", "source_uri": "fixture", "license": "CC0"}
        cases.append(canonical_json(doc) + b"\n" + canonical_json(doc))
        for payload in cases:
            path = f["root"] / "bad.jsonl"
            path.write_bytes(payload)
            with self.assertRaises(ValueError):
                build_registry(path)

    def test_untrusted_signer_and_modified_signature_rejected(self):
        f = self.fixture
        other = keygen(f["root"] / "other.private.pem", f["root"] / "other.key")
        with self.assertRaises(ValueError):
            validate_manifest(f["snapshot"].envelope, f["registry"], f["core"], other)
        forged = copy.deepcopy(f["snapshot"].envelope)
        forged["signature"] = "0" * 128
        with self.assertRaises(ValueError):
            validate_manifest(forged, f["registry"], f["core"], f["public"])

    def test_missing_or_reordered_source_coverage_rejected(self):
        f = self.fixture
        body = copy.deepcopy(f["snapshot"].body)
        body["shards"][0]["first_chunk"] = 1
        with self.assertRaises(ValueError):
            validate_manifest(sign(body, load_key(f["key_path"])), f["registry"], f["core"], f["public"])

    def test_fresh_challenge_and_honest_audit(self):
        challenge, response = self.challenge()
        self.assertTrue(self.verify(challenge, response)["accepted"])
        fresh, _ = self.challenge()
        self.assertNotEqual(challenge["nonce"], fresh["nonce"])
        self.assertFalse(self.verify(fresh, response)["accepted"])

    def test_full_chunk_audit_and_probability(self):
        f = self.fixture
        challenge, response = self.challenge(f["report"]["chunks"])
        result = self.verify(challenge, response)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["single_bad_chunk_detection_probability"], 1)

    def test_changed_source_token_proof_and_nonce_fail(self):
        challenge, response = self.challenge()
        for kind in ("raw", "token", "source_proof", "token_proof", "nonce", "missing", "duplicate"):
            altered = copy.deepcopy(response)
            if kind == "raw":
                altered["samples"][0]["raw_text"] = "x" + altered["samples"][0]["raw_text"][1:]
            elif kind == "token":
                altered["samples"][0]["token_ids"][0] = 100
            elif kind in ("source_proof", "token_proof"):
                altered["samples"][0][kind][0] = "0" * 64
            elif kind == "nonce":
                altered["nonce"] = "0" * 64
            elif kind == "missing":
                altered["samples"].pop()
            else:
                altered["samples"][1] = altered["samples"][0]
            with self.subTest(kind=kind):
                self.assertFalse(self.verify(challenge, altered)["accepted"])

    def test_disk_corruption_fails_full_verification_and_sampled_audit(self):
        f = self.fixture
        challenge, response = self.challenge(f["report"]["chunks"])
        path = f["snapshot"].directory / "000000.u32le"
        payload = bytearray(path.read_bytes())
        payload[0] ^= 1
        path.write_bytes(payload)
        with self.assertRaises(ValueError):
            verify_snapshot(f["snapshot"].directory, f["registry"], f["core"], f["public"])
        changed = f["snapshot"].respond(challenge["nonce"], challenge["positions"])
        self.assertFalse(self.verify(challenge, changed)["accepted"])

    def test_validly_signed_bad_tokenization_is_detected(self):
        f = self.fixture
        body = copy.deepcopy(f["snapshot"].body)
        shard = body["shards"][0]
        records = read_json(f["snapshot"].directory / "000000.records.json")
        payload = bytearray((f["snapshot"].directory / "000000.u32le").read_bytes())
        first_id = int.from_bytes(payload[:4], "little")
        payload[:4] = ((first_id + 1) % f["core"].vocab_size).to_bytes(4, "little")
        records[0]["sha256"] = sha256(bytes(payload[:records[0]["token_count"] * 4]))
        shard["tokens_sha256"] = sha256(bytes(payload))
        shard["records_sha256"] = sha256(canonical_json(records))
        shard["root"] = MerkleTree(b"token", map(canonical_json, records)).root
        envelope = sign(body, load_key(f["key_path"]))
        directory = f["store"] / "snapshots" / envelope["id"]
        shutil.copytree(f["snapshot"].directory, directory)
        (directory / "manifest.json").write_bytes(canonical_json(envelope))
        (directory / "000000.u32le").write_bytes(payload)
        (directory / "000000.records.json").write_bytes(canonical_json(records))
        # Every signature and file hash is consistent; only the claimed work is false.
        verify_snapshot(directory, f["registry"], f["core"], f["public"])
        bad = StoredSnapshot(f["store"], envelope["id"])
        challenge = make_challenge(envelope, f["registry"], f["core"], f["public"], f["report"]["chunks"])
        result = verify_response(envelope, f["registry"], f["core"], f["public"], challenge,
                                 bad.respond(challenge["nonce"], challenge["positions"]))
        self.assertFalse(result["accepted"])
        self.assertTrue(any("retokenization mismatch" in failure for failure in result["failures"]))
        self.assertFalse(any("proof mismatch" in failure for failure in result["failures"]))

    def test_invalid_samples_and_path_traversal_rejected(self):
        f = self.fixture
        for count in (0, True, 257, f["report"]["chunks"] + 1):
            with self.assertRaises(ValueError):
                make_challenge(f["snapshot"].envelope, f["registry"], f["core"], f["public"], count)
        with self.assertRaises(ValueError):
            StoredSnapshot(f["store"], "../../secret")
        with self.assertRaises(ValueError):
            f["snapshot"].respond("1" * 64, [0, 0])

    def test_signed_receipt_and_identity_files_not_replaced(self):
        f = self.fixture
        challenge, response = self.challenge()
        receipt = sign(self.verify(challenge, response), load_key(f["key_path"]))
        self.assertTrue(verify_signature(receipt, f["public"])["accepted"])
        with self.assertRaises(ValueError):
            keygen(f["key_path"], f["public_path"])

    def test_non_loopback_plaintext_requires_explicit_lab_option(self):
        with self.assertRaises(ValueError):
            PeerClient("http://example.com", "a" * 32)
        with self.assertRaises(ValueError):
            PeerClient("https://user:secret@example.com", "a" * 32)

    def test_shard_rollover_preserves_all_tokens(self):
        f = self.fixture
        doc = {"id": "large", "text": "hello with enough data to cross the shard boundary.\n" * 800,
               "source_uri": "fixture:large", "license": "CC0"}
        path = f["root"] / "large.jsonl"
        path.write_bytes(canonical_json(doc))
        registry_path = f["root"] / "large.source.json"
        register(path, registry_path, 1024)
        report = mine(path, registry_path, f["core"], f["key_path"], f["store"], shard_tokens=16384)
        self.assertGreater(report["shards"], 1)
        result = verify_snapshot(f["store"] / "snapshots" / report["snapshot_id"], load_registry(registry_path), f["core"], f["public"])
        self.assertEqual(result["verified_tokens"], report["tokens"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
