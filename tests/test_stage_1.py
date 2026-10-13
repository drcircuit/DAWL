"""Run with: python -m unittest discover -s tests -v"""

from __future__ import annotations

from dataclasses import asdict, replace
import itertools
import json
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
from time import perf_counter
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_ingestion import (
    BlockManifest, MinerNode, SourceManifest, TokenizerCore, ValidatorNode,
    detection_probability, verify_published_payload,
)
from data_ingestion.experiment import run_experiment, storage_metrics
from data_ingestion.protocol import AuditChallenge, MerkleTree, canonical_json, sha256, verify_proof
from data_ingestion.tokenizer_core import pack_tokens, unpack_tokens


def poison(block, positions):
    """Commit a malicious token file BEFORE sampling, keeping the trusted source fixed."""
    tokens = list(block.token_ids)
    for index in positions:
        tokens[index] = ord("x") if tokens[index] != ord("x") else ord("y")
    tree = MerkleTree(b"token", (struct.pack("<I", token) for token in tokens))
    manifest = replace(
        block.manifest, tokens_sha256=sha256(pack_tokens(tokens)), tokens_merkle_root=tree.root,
    )
    return replace(block, manifest=manifest, token_ids=tuple(tokens), token_tree=tree)


class TokenizerTests(unittest.TestCase):
    def test_unicode_whitespace_and_newlines_round_trip(self):
        text = "DAWL\r\n\t\x00 \u00e5\u00e6\u00f8 \U0001f30d e\u0301 \u00e9"
        core = TokenizerCore()
        self.assertEqual(core.decode(core.encode(text)), text)
        self.assertNotEqual(core.encode("e\u0301"), core.encode("\u00e9"))
        self.assertEqual(core.encode(""), ())

    def test_explicit_little_endian_uint32(self):
        self.assertEqual(pack_tokens((65, 0x1F30D)), b"A\x00\x00\x00\x0d\xf3\x01\x00")
        self.assertEqual(unpack_tokens(pack_tokens((0, 65, 0x10FFFF))), (0, 65, 0x10FFFF))

    def test_reject_invalid_tokens_and_truncated_payload(self):
        for token in (-1, True, 1.0, 0xD800, 0xDFFF, 0x110000):
            with self.subTest(token=token), self.assertRaises(ValueError):
                pack_tokens((token,))
        for payload in (b"A", struct.pack("<I", 0xD800)):
            with self.assertRaises(ValueError):
                unpack_tokens(payload)
        with self.assertRaises(UnicodeEncodeError):
            TokenizerCore().encode("\ud800")

    def test_embeddings_are_exact_float32_and_depend_on_definition(self):
        core = TokenizerCore()
        row = core.embedding_row(65)
        self.assertEqual(len(row), 512)
        values = struct.unpack("<128f", row)
        self.assertTrue(all(-1 <= value < 1 for value in values))
        self.assertEqual(row, struct.pack("<128f", *values))
        self.assertEqual(core.dense_embeddings((65, 65)), row + row)
        self.assertNotEqual(row, TokenizerCore(embedding_seed="another-seed").embedding_row(65))
        self.assertNotEqual(row, core.embedding_row(66))

    def test_determinism_across_fresh_processes_and_python_hash_seeds(self):
        script = (
            "from data_ingestion import MinerNode, TokenizerCore; "
            "from data_ingestion.protocol import sha256; "
            "b=MinerNode().ingest('DAWL\\r\\n'); "
            "print(b.manifest.block_id, sha256(TokenizerCore().embedding_row(65)))"
        )
        outputs = []
        for seed in ("1", "7654321"):
            environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONHASHSEED": seed}
            outputs.append(subprocess.check_output([sys.executable, "-c", script], env=environment))
        self.assertEqual(outputs[0], outputs[1])

    def test_invalid_embedding_definitions(self):
        for dimension in (0, -1, True, 1.5, 4097):
            with self.subTest(dimension=dimension), self.assertRaises(ValueError):
                TokenizerCore(embedding_dimension=dimension)
        with self.assertRaises(ValueError):
            TokenizerCore(embedding_seed="")


class MerkleTests(unittest.TestCase):
    def test_all_positions_across_odd_even_and_single_leaf_trees(self):
        for count in (1, 2, 3, 5, 8, 9, 17):
            values = [bytes([i]) for i in range(count)]
            tree = MerkleTree(b"source", values)
            for index, value in enumerate(values):
                proof = tree.proof(index)
                self.assertTrue(verify_proof(b"source", index, value, proof, tree.root, count))
                self.assertFalse(verify_proof(b"token", index, value, proof, tree.root, count))
                self.assertFalse(verify_proof(b"source", index, b"wrong", proof, tree.root, count))

    def test_index_binding_prevents_reusing_an_equal_value_at_another_position(self):
        tree = MerkleTree(b"source", [b"a", b"a", b"a", b"a"])
        self.assertFalse(verify_proof(b"source", 2, b"a", tree.proof(0), tree.root, 4))

    def test_reject_malformed_truncated_and_extended_proofs(self):
        tree = MerkleTree(b"source", [b"a", b"b", b"c"])
        proof = tree.proof(2)
        for invalid in (proof[:-1], proof + ("0" * 64,), ("not-a-hash",) + proof[1:], None):
            self.assertFalse(verify_proof(b"source", 2, b"c", invalid, tree.root, 3))
        self.assertFalse(verify_proof(b"source", -1, b"c", proof, tree.root, 3))
        self.assertFalse(verify_proof(b"source", True, b"c", proof, tree.root, 3))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.block = MinerNode().ingest("A deterministic block.\r\n", "fixture:manifest")

    def test_canonical_round_trip_and_signing_domain(self):
        manifest = self.block.manifest
        restored = BlockManifest.from_bytes(manifest.canonical_bytes())
        self.assertEqual(restored, manifest)
        self.assertEqual(restored.block_id, manifest.block_id)
        self.assertEqual(manifest.block_id, sha256(manifest.signing_bytes()))
        self.assertNotEqual(manifest.signing_bytes(), manifest.canonical_bytes())
        self.assertNotEqual(manifest.block_id, replace(manifest, embedding_seed="other").block_id)

    def test_metadata_and_source_hash_bind_exact_input(self):
        source = self.block.manifest.source
        self.assertEqual(source.content_sha256, sha256(self.block.source_text.encode("utf-8")))
        changed = MinerNode().ingest(self.block.source_text.replace("\r\n", "\n"), source.source_id)
        self.assertNotEqual(changed.manifest.block_id, self.block.manifest.block_id)

    def test_reject_unknown_duplicate_and_missing_keys(self):
        value = asdict(self.block.manifest)
        variants = [dict(value, unexpected=1), {k: v for k, v in value.items() if k != "source"}]
        for variant in variants:
            with self.assertRaises(ValueError):
                BlockManifest.from_bytes(canonical_json(variant))
        duplicate = self.block.manifest.canonical_bytes()[:-1] + b',"schema_version":1}'
        with self.assertRaises(ValueError):
            BlockManifest.from_bytes(duplicate)

    def test_reject_invalid_field_types_values_and_schema(self):
        for key, bad in (
            ("token_count", True), ("token_count", -1), ("token_count", 1_000_001),
            ("schema_version", 2), ("tokenizer_id", "another"),
            ("tokens_sha256", "not a digest"), ("embedding_dimension", 0),
        ):
            value = asdict(self.block.manifest)
            value[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                BlockManifest.from_bytes(canonical_json(value))
        with self.assertRaises(ValueError):
            BlockManifest.from_bytes(b"x" * 20_000)

    def test_full_payload_verification_detects_tampering_and_root_disagreement(self):
        self.assertTrue(verify_published_payload(self.block.manifest, self.block.token_payload))
        self.assertFalse(verify_published_payload(self.block.manifest, self.block.token_payload[:-1]))
        bad = b"B" + self.block.token_payload[1:]
        self.assertFalse(verify_published_payload(self.block.manifest, bad))
        manifest = replace(self.block.manifest, tokens_sha256=sha256(bad))
        self.assertFalse(verify_published_payload(manifest, bad))

    def test_empty_blocks_are_rejected(self):
        with self.assertRaises(ValueError):
            MinerNode().ingest("")
        with self.assertRaises(ValueError):
            SourceManifest.from_text("", "empty")

    def test_publish_artifact_and_prevent_accidental_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.block.publish(directory)
            self.assertEqual(path.name, self.block.manifest.block_id)
            self.assertEqual((path / "manifest.json").read_bytes(), self.block.manifest.canonical_bytes())
            self.assertTrue(verify_published_payload(
                BlockManifest.from_bytes((path / "manifest.json").read_bytes()),
                (path / "tokens.u32le").read_bytes(),
            ))
            with self.assertRaises(FileExistsError):
                self.block.publish(directory)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.text = "Deterministic \u00e5\u00e6\u00f8 \U0001f30d\r\nspot checks."
        self.trusted = SourceManifest.from_text(self.text, "fixture:audit")
        self.block = MinerNode().ingest(self.text, "fixture:audit")
        self.validator = ValidatorNode(self.trusted)
        self.challenge = self.validator.issue_challenge(self.block.manifest, 5, test_rng=random.Random(8))
        self.response = self.block.respond(self.challenge)

    def verify(self, response):
        return self.validator.verify(self.block.manifest, self.challenge, response)

    def test_honest_sample_and_full_audit(self):
        self.assertTrue(self.verify(self.response).accepted)
        challenge = self.validator.issue_challenge(self.block.manifest, len(self.text))
        self.assertTrue(self.validator.verify(self.block.manifest, challenge, self.block.respond(challenge)).accepted)

    def test_default_challenge_is_fresh_distinct_and_in_range(self):
        first = self.validator.issue_challenge(self.block.manifest, 8)
        second = self.validator.issue_challenge(self.block.manifest, 8)
        self.assertNotEqual(first.nonce, second.nonce)
        self.assertEqual(len(set(first.positions)), 8)
        self.assertTrue(all(0 <= index < len(self.text) for index in first.positions))

    def test_invalid_sample_counts(self):
        for count in (0, -1, len(self.text) + 1, True, 2.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.validator.issue_challenge(self.block.manifest, count)

    def test_reject_replayed_or_misbound_responses(self):
        self.assertFalse(self.verify(replace(self.response, nonce="0" * 32)).accepted)
        self.assertFalse(self.verify(replace(self.response, block_id="0" * 64)).accepted)
        fresh = self.validator.issue_challenge(self.block.manifest, 5)
        self.assertFalse(self.validator.verify(self.block.manifest, fresh, self.response).accepted)

    def test_reject_missing_duplicate_extra_and_unexpected_samples(self):
        samples = self.response.samples
        variants = (
            samples[:-1], samples + samples[:1], (samples[0],) * len(samples),
            (replace(samples[0], index=-1),) + samples[1:],
        )
        for invalid in variants:
            with self.subTest(invalid=invalid):
                self.assertFalse(self.verify(replace(self.response, samples=invalid)).accepted)

    def test_reject_forged_snippet_token_and_proof(self):
        sample = self.response.samples[0]
        variants = (
            replace(sample, raw_snippet="!", token_id=33),
            replace(sample, token_id=sample.token_id + 1),
            replace(sample, source_proof=("0" * 64,) + sample.source_proof[1:]),
            replace(sample, token_proof=sample.token_proof[:-1]),
            replace(sample, raw_snippet="too long"),
            replace(sample, raw_snippet="\ud800"),
            replace(sample, token_id=True),
        )
        for invalid in variants:
            self.assertFalse(self.verify(replace(self.response, samples=(invalid,) + self.response.samples[1:])).accepted)

    def test_malformed_objects_fail_closed(self):
        self.assertFalse(self.verify(None).accepted)
        self.assertFalse(self.verify(replace(self.response, samples=(None,) * 5)).accepted)
        challenge = replace(self.challenge, positions=(True,))
        self.assertFalse(self.validator.verify(self.block.manifest, challenge, self.response).accepted)

    def test_source_substitution_and_embedding_changes_rejected(self):
        other = MinerNode().ingest("x" * len(self.text), "fixture:audit")
        with self.assertRaises(ValueError):
            self.validator.issue_challenge(other.manifest, 1)
        for manifest in (
            replace(self.block.manifest, embedding_seed="another"),
            replace(self.block.manifest, embedding_dimension=64),
        ):
            with self.assertRaises(ValueError):
                self.validator.issue_challenge(manifest, 1)

    def test_sparse_error_can_be_missed(self):
        bad = poison(self.block, (0,))
        challenge = AuditChallenge(bad.manifest.block_id, "a" * 32, (1,))
        self.assertTrue(self.validator.verify(bad.manifest, challenge, bad.respond(challenge)).accepted)
        hit = replace(challenge, positions=(0,))
        self.assertFalse(self.validator.verify(bad.manifest, hit, bad.respond(hit)).accepted)

    def test_exhaustive_samples_match_hypergeometric_prediction(self):
        text = "abcdefg"
        block = MinerNode().ingest(text)
        validator = ValidatorNode(SourceManifest.from_text(text, "local:input"))
        bad = poison(block, (0, 3))
        detected = 0
        samples = list(itertools.combinations(range(7), 2))
        for positions in samples:
            challenge = AuditChallenge(bad.manifest.block_id, "b" * 32, positions)
            detected += not validator.verify(bad.manifest, challenge, bad.respond(challenge)).accepted
        self.assertEqual(detected, len(samples) - math.comb(5, 2))
        self.assertAlmostEqual(detected / len(samples), detection_probability(7, 2, 2))

    def test_probability_boundary_cases(self):
        self.assertEqual(detection_probability(100, 0, 5), 0)
        self.assertEqual(detection_probability(100, 100, 1), 1)
        self.assertEqual(detection_probability(100, 10, 0), 0)
        self.assertEqual(detection_probability(100, 1, 100), 1)
        self.assertAlmostEqual(detection_probability(1000, 1, 7), 0.007)
        for arguments in ((0, 0, 0), (10, 11, 1), (10, 1, 11), (10, -1, 1), (True, 1, 1)):
            with self.assertRaises(ValueError):
                detection_probability(*arguments)


class HypothesisTests(unittest.TestCase):
    def test_h1_1_exact_storage_and_recovery(self):
        text = "Open data, verifiable work. \u00e5\u00e6\u00f8 \U0001f30d\r\n" * 256
        core = TokenizerCore()
        block = MinerNode(core).ingest(text, "fixture:h1.1")
        dense = core.dense_embeddings(block.token_ids)
        metrics = storage_metrics(block, dense)
        restored = unpack_tokens(block.token_payload)
        self.assertEqual(TokenizerCore().dense_embeddings(restored), dense)
        self.assertEqual(metrics["token_payload_bytes"], len(text) * 4)
        self.assertEqual(metrics["dense_float32_bytes"], len(text) * 128 * 4)
        self.assertEqual(metrics["payload_savings_percent"], 99.21875)
        self.assertGreater(metrics["published_savings_percent"], 99)
        self.assertGreater(metrics["published_bytes"], metrics["raw_utf8_bytes"])
        print("\nH1.1 exact byte accounting:", json.dumps(metrics, sort_keys=True))

    def test_h1_1_small_blocks_and_smaller_dimensions_disprove_universal_claim(self):
        for text, dimension in (("a", 128), ("a" * 4096, 64)):
            core = TokenizerCore(embedding_dimension=dimension)
            block = MinerNode(core).ingest(text)
            metrics = storage_metrics(block, core.dense_embeddings(block.token_ids))
            self.assertLess(metrics["published_savings_percent"], 99)

    def test_h1_2_precommitted_poison_is_detected_with_measured_cost(self):
        text = "DAWL work can be checked.\n" * 2048
        trusted = SourceManifest.from_text(text, "fixture:h1.2")
        start = perf_counter()
        block = MinerNode().ingest(text, "fixture:h1.2")
        ingestion_ms = (perf_counter() - start) * 1000
        bad = poison(block, range(len(text)))
        validator = ValidatorNode(trusted)
        # All positions are poisoned before drawing. No lucky seed or post-draw tampering.
        start = perf_counter()
        challenge = validator.issue_challenge(bad.manifest, 64)
        result = validator.verify(bad.manifest, challenge, bad.respond(challenge))
        audit_ms = (perf_counter() - start) * 1000
        self.assertFalse(result.accepted)
        self.assertEqual(result.checked_count, 64)
        self.assertEqual(sum("token/source mismatch" in failure for failure in result.failures), 64)
        self.assertFalse(any("proof failed" in failure for failure in result.failures))
        print("\nH1.2 committed poison:", json.dumps({
            "tokens": len(text), "corrupt_positions": len(text), "samples": 64,
            "detected": not result.accepted, "ingestion_ms": ingestion_ms,
            "audit_ms": audit_ms, "audit_to_ingestion_percent": 100 * audit_ms / ingestion_ms,
            "note": "No universal timing assertion; all-token corruption has P(detect)=1.",
        }, sort_keys=True))

    def test_end_to_end_report_and_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            report = run_experiment("A small demo.\n" * 32, sample_count=5, repeats=2, output=directory)
            self.assertTrue(report["audit"]["accepted"])
            self.assertEqual(len(report["timing"]["audit_trials"]), 2)
            saved = json.loads((Path(report["artifact_directory"]) / "metrics.json").read_bytes())
            self.assertEqual(saved, report)

    def test_cli_preserves_file_bytes_and_reports_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            source.write_bytes(b"DAWL\r\n")
            output = subprocess.check_output([
                sys.executable, str(ROOT / "dawl.py"), "--input", str(source),
                "--samples", "2", "--repeats", "1",
            ])
            report = json.loads(output)
            self.assertEqual(report["storage"]["raw_utf8_bytes"], 6)
            self.assertEqual(report["storage"]["token_count"], 6)
            failed = subprocess.run(
                [sys.executable, str(ROOT / "dawl.py"), "--samples", "0"], capture_output=True,
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn(b"sample count", failed.stderr)
            self.assertNotIn(b"Traceback", failed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
