"""Local miner: commit exact source bytes and token IDs before receiving challenges."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import struct

from .protocol import (
    AuditChallenge, AuditResponse, BlockManifest, MerkleTree, SampleEvidence,
    SourceManifest, require_count, sha256,
)
from .tokenizer_core import EMBEDDING_ID, TOKENIZER_ID, TokenizerCore, pack_tokens

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class MinedBlock:
    manifest: BlockManifest
    token_ids: tuple[int, ...]
    source_text: str
    source_tree: MerkleTree
    token_tree: MerkleTree

    @property
    def token_payload(self) -> bytes:
        return pack_tokens(self.token_ids)

    @property
    def cached_digest_bytes(self) -> int:
        return self.source_tree.cached_digest_bytes + self.token_tree.cached_digest_bytes

    def respond(self, challenge: AuditChallenge) -> AuditResponse:
        if challenge.block_id != self.manifest.block_id:
            raise ValueError("challenge belongs to a different block")
        if not challenge.positions or len(set(challenge.positions)) != len(challenge.positions):
            raise ValueError("challenge positions must be nonempty and distinct")
        samples = []
        for index in challenge.positions:
            if type(index) is not int or not 0 <= index < len(self.token_ids):
                raise ValueError("challenge position out of range")
            samples.append(SampleEvidence(
                index, self.source_text[index], self.token_ids[index],
                self.source_tree.proof(index), self.token_tree.proof(index),
            ))
        return AuditResponse(challenge.block_id, challenge.nonce, tuple(samples))

    def publish(self, directory: str | Path) -> Path:
        """Write a new local content-addressed block directory; never overwrite one."""
        block_path = Path(directory) / self.manifest.block_id
        block_path.mkdir(parents=True, exist_ok=False)
        (block_path / "manifest.json").write_bytes(self.manifest.canonical_bytes())
        (block_path / "tokens.u32le").write_bytes(self.token_payload)
        return block_path


class MinerNode:
    def __init__(self, tokenizer: TokenizerCore | None = None) -> None:
        self.tokenizer = tokenizer or TokenizerCore()

    def ingest(self, text: str, source_id: str = "local:input") -> MinedBlock:
        require_count(len(text))
        token_ids = self.tokenizer.encode(text)
        raw = text.encode("utf-8", errors="strict")
        payload = pack_tokens(token_ids)
        source_tree = MerkleTree(b"source", (char.encode("utf-8") for char in text))
        token_tree = MerkleTree(b"token", (struct.pack("<I", token) for token in token_ids))
        source = SourceManifest(source_id, sha256(raw), len(raw), len(token_ids), source_tree.root)
        manifest = BlockManifest(
            1, TOKENIZER_ID, EMBEDDING_ID,
            self.tokenizer.embedding_dimension, self.tokenizer.embedding_seed,
            source, len(token_ids), sha256(payload), token_tree.root,
        )
        LOGGER.info(
            "Committed source=%r source_sha256=%s tokens=%d block_id=%s",
            source_id, source.content_sha256, len(token_ids), manifest.block_id,
        )
        return MinedBlock(manifest, token_ids, text, source_tree, token_tree)
