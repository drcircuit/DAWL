"""Canonical commitments and indexed Merkle proofs for local Stage 1 experiments."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re
import struct
from typing import Iterable

from .tokenizer_core import EMBEDDING_ID, TOKENIZER_ID, TokenizerCore

MAX_TOKENS = 1_000_000
MAX_MANIFEST_BYTES = 16_384
_HEX256 = re.compile(r"[0-9a-f]{64}\Z")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require_digest(value: str) -> None:
    if not isinstance(value, str) or not _HEX256.fullmatch(value):
        raise ValueError("expected a lowercase SHA-256 hex digest")


def require_count(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_TOKENS:
        raise ValueError(f"token count must be in [1, {MAX_TOKENS}]")


def _leaf(domain: bytes, index: int, value: bytes) -> bytes:
    return hashlib.sha256(
        b"\x00DAWL/leaf/v1/" + domain + b"\x00" + struct.pack("<QI", index, len(value)) + value
    ).digest()


def _parent(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01DAWL/node/v1/" + left + right).digest()


class MerkleTree:
    """Ordered binary tree; duplicate the final node at an odd-width level."""

    def __init__(self, domain: bytes, values: Iterable[bytes]) -> None:
        if domain not in (b"source", b"token"):
            raise ValueError("unknown leaf domain")
        leaves = tuple(_leaf(domain, i, value) for i, value in enumerate(values))
        require_count(len(leaves))
        levels = [leaves]
        while len(levels[-1]) > 1:
            level = levels[-1]
            levels.append(tuple(
                _parent(level[i], level[i + 1] if i + 1 < len(level) else level[i])
                for i in range(0, len(level), 2)
            ))
        self._levels = tuple(levels)

    @property
    def root(self) -> str:
        return self._levels[-1][0].hex()

    @property
    def cached_digest_bytes(self) -> int:
        """Digest storage only; excludes Python container/object overhead."""
        return 32 * sum(map(len, self._levels))

    def proof(self, index: int) -> tuple[str, ...]:
        if type(index) is not int or not 0 <= index < len(self._levels[0]):
            raise ValueError("proof index out of range")
        siblings = []
        for level in self._levels[:-1]:
            sibling = index ^ 1
            siblings.append(level[sibling if sibling < len(level) else index].hex())
            index //= 2
        return tuple(siblings)


def verify_proof(
    domain: bytes, index: int, value: bytes, siblings: tuple[str, ...],
    root: str, leaf_count: int,
) -> bool:
    try:
        require_count(leaf_count)
        require_digest(root)
        if domain not in (b"source", b"token") or not isinstance(value, bytes):
            return False
        if type(index) is not int or not 0 <= index < leaf_count:
            return False
        if not isinstance(siblings, tuple) or len(siblings) != (leaf_count - 1).bit_length():
            return False
        digest = _leaf(domain, index, value)
        width = leaf_count
        for sibling_hex in siblings:
            require_digest(sibling_hex)
            sibling = bytes.fromhex(sibling_hex)
            if index % 2 == 0:
                if index + 1 == width and sibling != digest:
                    return False
                digest = _parent(digest, sibling)
            else:
                digest = _parent(sibling, digest)
            index //= 2
            width = (width + 1) // 2
        return digest.hex() == root
    except (TypeError, ValueError, OverflowError, struct.error):
        return False


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    content_sha256: str
    utf8_bytes: int
    token_count: int
    merkle_root: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id or len(self.source_id) > 512:
            raise ValueError("source ID must contain 1..512 characters")
        self.source_id.encode("utf-8", errors="strict")
        require_count(self.token_count)
        if type(self.utf8_bytes) is not int or not self.token_count <= self.utf8_bytes <= 4 * self.token_count:
            raise ValueError("source byte count is inconsistent with Unicode scalar count")
        require_digest(self.content_sha256)
        require_digest(self.merkle_root)

    @classmethod
    def from_text(cls, text: str, source_id: str) -> SourceManifest:
        """O(N) source registration. Trust comes from the caller's source policy."""
        payload = text.encode("utf-8", errors="strict")
        require_count(len(text))
        tree = MerkleTree(b"source", (char.encode("utf-8") for char in text))
        return cls(source_id, sha256(payload), len(payload), len(text), tree.root)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _require_keys(value: dict, model: type) -> None:
    if not isinstance(value, dict) or set(value) != {field.name for field in fields(model)}:
        raise ValueError(f"invalid {model.__name__} fields")


@dataclass(frozen=True)
class BlockManifest:
    schema_version: int
    tokenizer_id: str
    embedding_id: str
    embedding_dimension: int
    embedding_seed: str
    source: SourceManifest
    token_count: int
    tokens_sha256: str
    tokens_merkle_root: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported block schema")
        if self.tokenizer_id != TOKENIZER_ID or self.embedding_id != EMBEDDING_ID:
            raise ValueError("unsupported tokenizer or embedding algorithm")
        TokenizerCore(self.embedding_dimension, self.embedding_seed)
        if not isinstance(self.source, SourceManifest):
            raise ValueError("invalid source manifest")
        require_count(self.token_count)
        if self.token_count != self.source.token_count:
            raise ValueError("source and token counts differ")
        require_digest(self.tokens_sha256)
        require_digest(self.tokens_merkle_root)

    def canonical_bytes(self) -> bytes:
        return canonical_json(asdict(self))

    def signing_bytes(self) -> bytes:
        """Feed these exact bytes to a separately chosen signature implementation."""
        return b"DAWL/block-manifest/v1\x00" + self.canonical_bytes()

    @property
    def block_id(self) -> str:
        return sha256(self.signing_bytes())

    @classmethod
    def from_bytes(cls, payload: bytes) -> BlockManifest:
        if not isinstance(payload, bytes) or len(payload) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds size limit or is not bytes")
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
            _require_keys(value, cls)
            _require_keys(value["source"], SourceManifest)
            value["source"] = SourceManifest(**value["source"])
            return cls(**value)
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(f"invalid block manifest: {error}") from error


@dataclass(frozen=True)
class AuditChallenge:
    block_id: str
    nonce: str
    positions: tuple[int, ...]


@dataclass(frozen=True)
class SampleEvidence:
    index: int
    raw_snippet: str
    token_id: int
    source_proof: tuple[str, ...]
    token_proof: tuple[str, ...]


@dataclass(frozen=True)
class AuditResponse:
    block_id: str
    nonce: str
    samples: tuple[SampleEvidence, ...]

    def wire_bytes(self) -> bytes:
        """Concrete JSON response encoding used for byte accounting."""
        return canonical_json(asdict(self))
