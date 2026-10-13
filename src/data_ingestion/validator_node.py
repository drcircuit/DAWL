"""Deterministic spot checks with unpredictable sampling after commitment."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import secrets

from .protocol import (
    AuditChallenge, AuditResponse, BlockManifest, SourceManifest, verify_proof,
)
from .tokenizer_core import TokenizerCore, pack_tokens, unpack_tokens
from .protocol import MerkleTree, sha256
import struct


def detection_probability(total: int, corrupted: int, samples: int) -> float:
    """Uniform draws without replacement; corruption is fixed before the draw."""
    if any(type(value) is not int for value in (total, corrupted, samples)):
        raise ValueError("counts must be integers")
    if total <= 0 or not 0 <= corrupted <= total or not 0 <= samples <= total:
        raise ValueError("invalid population, corruption, or sample count")
    if corrupted == 0 or samples == 0:
        return 0.0
    if samples > total - corrupted:
        return 1.0
    log_miss = math.fsum(math.log1p(-corrupted / (total - i)) for i in range(samples))
    return -math.expm1(log_miss)


@dataclass(frozen=True)
class AuditResult:
    accepted: bool
    checked_count: int
    failures: tuple[str, ...]


class ValidatorNode:
    def __init__(self, trusted_source: SourceManifest, tokenizer: TokenizerCore | None = None) -> None:
        """trusted_source must come from the caller's independently accepted source."""
        self.trusted_source = trusted_source
        self.tokenizer = tokenizer or TokenizerCore()

    def validate_manifest(self, manifest: BlockManifest) -> None:
        if not isinstance(manifest, BlockManifest):
            raise ValueError("invalid manifest type")
        if manifest.source != self.trusted_source:
            raise ValueError("source does not match the independently pinned commitment")
        if (
            manifest.embedding_dimension != self.tokenizer.embedding_dimension
            or manifest.embedding_seed != self.tokenizer.embedding_seed
        ):
            raise ValueError("embedding definition does not match the validator")

    def issue_challenge(
        self, manifest: BlockManifest, sample_count: int,
        *, test_rng: random.Random | None = None,
    ) -> AuditChallenge:
        """test_rng is for reproducible tests only. Production default uses OS entropy."""
        self.validate_manifest(manifest)
        if type(sample_count) is not int or not 1 <= sample_count <= manifest.token_count:
            raise ValueError("sample count must be in [1, token_count]")
        sampler = test_rng if test_rng is not None else secrets.SystemRandom()
        positions = tuple(sorted(sampler.sample(range(manifest.token_count), sample_count)))
        return AuditChallenge(manifest.block_id, secrets.token_hex(16), positions)

    def verify(
        self, manifest: BlockManifest, challenge: AuditChallenge, response: AuditResponse,
    ) -> AuditResult:
        """Use the validator's retained challenge, never one supplied by the miner."""
        try:
            self.validate_manifest(manifest)
            if not isinstance(challenge, AuditChallenge) or not isinstance(response, AuditResponse):
                raise ValueError("invalid challenge or response type")
            if challenge.block_id != manifest.block_id or response.block_id != challenge.block_id:
                raise ValueError("block commitment mismatch")
            if (
                not isinstance(challenge.nonce, str) or len(challenge.nonce) != 32
                or any(char not in "0123456789abcdef" for char in challenge.nonce)
                or response.nonce != challenge.nonce
            ):
                raise ValueError("challenge nonce mismatch")
            positions = challenge.positions
            if (
                not isinstance(positions, tuple) or not positions
                or any(type(i) is not int or not 0 <= i < manifest.token_count for i in positions)
                or len(set(positions)) != len(positions)
            ):
                raise ValueError("invalid challenge positions")
            if not isinstance(response.samples, tuple) or len(response.samples) != len(positions):
                raise ValueError("missing or extra evidence")
            indexes = tuple(sample.index for sample in response.samples)
            if any(type(i) is not int for i in indexes) or sorted(indexes) != sorted(positions):
                raise ValueError("duplicate, unexpected, or missing sample index")
            failures = []
            for sample in response.samples:
                if not isinstance(sample.raw_snippet, str) or len(sample.raw_snippet) != 1:
                    raise ValueError("a snippet must contain exactly one Unicode scalar")
                source_bytes = sample.raw_snippet.encode("utf-8", errors="strict")
                token_bytes = pack_tokens((sample.token_id,))
                if not verify_proof(
                    b"source", sample.index, source_bytes, sample.source_proof,
                    self.trusted_source.merkle_root, manifest.token_count,
                ):
                    failures.append(f"source proof failed at {sample.index}")
                if not verify_proof(
                    b"token", sample.index, token_bytes, sample.token_proof,
                    manifest.tokens_merkle_root, manifest.token_count,
                ):
                    failures.append(f"token proof failed at {sample.index}")
                if self.tokenizer.encode(sample.raw_snippet) != (sample.token_id,):
                    failures.append(f"token/source mismatch at {sample.index}")
            return AuditResult(not failures, len(response.samples), tuple(failures))
        except (ValueError, TypeError, AttributeError, OverflowError, struct.error) as error:
            return AuditResult(False, 0, (str(error),))


def verify_published_payload(manifest: BlockManifest, payload: bytes) -> bool:
    """O(N) check for consumers downloading a complete token file, not a spot check.

    This checks that the published SHA-256 and Merkle root describe the same IDs.
    Source consistency still requires auditing or comparing to the trusted source.
    """
    if len(payload) != manifest.token_count * 4 or sha256(payload) != manifest.tokens_sha256:
        return False
    try:
        tokens = unpack_tokens(payload)
        tree = MerkleTree(b"token", (struct.pack("<I", token) for token in tokens))
        return tree.root == manifest.tokens_merkle_root
    except (ValueError, TypeError, struct.error):
        return False
