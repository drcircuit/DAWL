"""Versioned Unicode toy vocabulary and a reproducible, synthetic embedding table."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import struct
from typing import Iterable

TOKENIZER_ID = "unicode-scalar-v1"
EMBEDDING_ID = "shake256-u16-f32le-v1"
DEFAULT_SEED = "dawl-stage-1"


def validate_token(token_id: int) -> None:
    if type(token_id) is not int or not 0 <= token_id <= 0x10FFFF:
        raise ValueError("token ID must be a Unicode scalar integer")
    if 0xD800 <= token_id <= 0xDFFF:
        raise ValueError("surrogate code points are not tokens")


def pack_tokens(token_ids: Iterable[int]) -> bytes:
    result = bytearray()
    for token_id in token_ids:
        validate_token(token_id)
        result.extend(struct.pack("<I", token_id))
    return bytes(result)


def unpack_tokens(payload: bytes) -> tuple[int, ...]:
    if len(payload) % 4:
        raise ValueError("truncated uint32 token payload")
    tokens = tuple(item[0] for item in struct.iter_unpack("<I", payload))
    for token_id in tokens:
        validate_token(token_id)
    return tokens


@lru_cache(maxsize=4096)
def _embedding_row(token_id: int, dimension: int, seed: str) -> bytes:
    encoded_seed = seed.encode("utf-8")
    definition = (
        b"DAWL/embedding/shake256-u16-f32le-v1\x00"
        + struct.pack("<III", len(encoded_seed), dimension, token_id)
        + encoded_seed
    )
    integers = struct.unpack(
        f"<{dimension}H", hashlib.shake_256(definition).digest(2 * dimension)
    )
    return struct.pack(f"<{dimension}f", *((u - 32768) / 32768 for u in integers))


@dataclass(frozen=True)
class TokenizerCore:
    """No normalization, fitted vocabulary, unknown-token fallback, or learned weights."""

    embedding_dimension: int = 128
    embedding_seed: str = DEFAULT_SEED

    def __post_init__(self) -> None:
        if type(self.embedding_dimension) is not int or not 1 <= self.embedding_dimension <= 4096:
            raise ValueError("embedding dimension must be an integer in [1, 4096]")
        if not isinstance(self.embedding_seed, str) or not self.embedding_seed:
            raise ValueError("embedding seed must be a nonempty string")
        if len(self.embedding_seed.encode("utf-8")) > 256:
            raise ValueError("embedding seed exceeds 256 bytes")

    def encode(self, text: str) -> tuple[int, ...]:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        text.encode("utf-8", errors="strict")
        return tuple(map(ord, text))

    def decode(self, token_ids: Iterable[int]) -> str:
        tokens = tuple(token_ids)
        for token_id in tokens:
            validate_token(token_id)
        return "".join(map(chr, tokens))

    def embedding_row(self, token_id: int) -> bytes:
        validate_token(token_id)
        return _embedding_row(token_id, self.embedding_dimension, self.embedding_seed)

    def dense_embeddings(self, token_ids: Iterable[int]) -> bytes:
        """Materialize actual float32 bytes, for small experimental corpora only."""
        return b"".join(self.embedding_row(token_id) for token_id in token_ids)
