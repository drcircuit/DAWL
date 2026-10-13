"""Reproducible byte accounting and explicitly scoped local timing measurements."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import platform
import statistics
from time import perf_counter_ns
from typing import Callable, TypeVar

from .miner_node import MinerNode, MinedBlock
from .protocol import SourceManifest, canonical_json
from .tokenizer_core import TokenizerCore
from .validator_node import ValidatorNode, detection_probability, verify_published_payload

T = TypeVar("T")
DEFAULT_TEXT = (
    "DAWL is an experiment in shared, verifiable AI infrastructure. "
    "Open protocols, reproducible evidence, and independent participation.\n"
) * 1024


def _timed(function: Callable[[], T]) -> tuple[T, float]:
    start = perf_counter_ns()
    result = function()
    return result, (perf_counter_ns() - start) / 1_000_000


def storage_metrics(block: MinedBlock, dense: bytes) -> dict:
    expected_bytes = block.manifest.token_count * block.manifest.embedding_dimension * 4
    if len(dense) != expected_bytes:
        raise ValueError("dense array length does not match the block definition")
    raw_bytes = len(block.source_text.encode("utf-8"))
    token_bytes = len(block.token_payload)
    manifest_bytes = len(block.manifest.canonical_bytes())
    published_bytes = token_bytes + manifest_bytes
    return {
        "token_count": block.manifest.token_count,
        "embedding_dimension": block.manifest.embedding_dimension,
        "raw_utf8_bytes": raw_bytes,
        "dense_float32_bytes": len(dense),
        "token_payload_bytes": token_bytes,
        "canonical_manifest_bytes": manifest_bytes,
        "published_bytes": published_bytes,
        "payload_savings_percent": 100 * (1 - token_bytes / len(dense)),
        "published_savings_percent": 100 * (1 - published_bytes / len(dense)),
        "published_to_raw_ratio": published_bytes / raw_bytes,
        "miner_cached_digest_bytes": block.cached_digest_bytes,
    }


def run_experiment(
    text: str = DEFAULT_TEXT, source_id: str = "fixture:dawl-stage-1",
    sample_count: int = 64, repeats: int = 5,
    output: str | Path | None = None,
) -> dict:
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeat count must be positive")
    if type(sample_count) is not int or not 1 <= sample_count <= len(text):
        raise ValueError("sample count must be in [1, text character count]")
    tokenizer = TokenizerCore()
    # In deployment the accepted source must be independent of the miner.
    trusted, registration_ms = _timed(lambda: SourceManifest.from_text(text, source_id))
    miner = MinerNode(tokenizer)
    block, ingestion_ms = _timed(lambda: miner.ingest(text, source_id))
    validator = ValidatorNode(trusted, tokenizer)
    validator.validate_manifest(block.manifest)
    dense, dense_ms = _timed(lambda: tokenizer.dense_embeddings(block.token_ids))
    storage = storage_metrics(block, dense)
    del dense

    trials = []
    response_bytes = 0
    for _ in range(repeats):
        start = perf_counter_ns()
        challenge, challenge_ms = _timed(lambda: validator.issue_challenge(block.manifest, sample_count))
        response, response_ms = _timed(lambda: block.respond(challenge))
        result, verify_ms = _timed(lambda: validator.verify(block.manifest, challenge, response))
        total_ms = (perf_counter_ns() - start) / 1_000_000
        if not result.accepted:
            raise RuntimeError(f"honest fixture failed audit: {result.failures}")
        response_bytes = len(response.wire_bytes())
        trials.append({
            "challenge_ms": challenge_ms, "response_ms": response_ms,
            "verification_ms": verify_ms, "total_ms": total_ms,
        })

    audit_ms = statistics.median(trial["total_ms"] for trial in trials)
    payload_valid, payload_verify_ms = _timed(
        lambda: verify_published_payload(block.manifest, block.token_payload)
    )
    if not payload_valid:
        raise RuntimeError("published token payload failed full verification")
    report = {
        "protocol": "commit-before-challenge deterministic spot-check audit",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "block_id": block.manifest.block_id,
        "storage": storage,
        "audit": {
            "accepted": True,
            "sample_count": sample_count,
            "sample_fraction_percent": 100 * sample_count / len(text),
            "last_response_json_bytes": response_bytes,
            "single_error_detection_probability": detection_probability(len(text), 1, sample_count),
            "one_percent_corruption_detection_probability": detection_probability(
                len(text), max(1, len(text) // 100), sample_count
            ),
            "assumed_corrupt_positions_for_one_percent": max(1, len(text) // 100),
        },
        "timing": {
            "source_registration_ms": registration_ms,
            "miner_ingestion_ms": ingestion_ms,
            "dense_materialization_ms": dense_ms,
            "full_payload_verification_ms": payload_verify_ms,
            "audit_trials": trials,
            "median_audit_ms": audit_ms,
            "median_audit_to_ingestion_percent": 100 * audit_ms / ingestion_ms,
        },
        "scope": (
            "Local CPU measurements; no network or disk in protocol timings. "
            "Source registration is separate O(N) trusted setup. Published bytes exclude "
            "source retention, caches, signatures, replication, and audit traffic. "
            "Cached digest bytes exclude Python object overhead. Timings are observations. "
            "No zero-knowledge, semantic-quality, or complete-possession claim."
        ),
    }
    if output is not None:
        destination = block.publish(output)
        report["artifact_directory"] = str(destination.resolve())
        (destination / "source_manifest.json").write_bytes(canonical_json(asdict(trusted)))
        (destination / "metrics.json").write_bytes(canonical_json(report))
    return report
