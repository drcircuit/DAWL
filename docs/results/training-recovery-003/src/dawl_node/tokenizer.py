"""Pinned real ByteLevel BPE artifacts; tokenization only, no tokenizer fitting."""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from tokenizers import Tokenizer

from .common import parse_json, read_bytes, sha256


class PinnedTokenizer:
    def __init__(self, path: Path, expected_sha256: str):
        self.payload = read_bytes(path, 16 * 1024 * 1024)
        self.sha256 = sha256(self.payload)
        if self.sha256 != expected_sha256:
            raise ValueError("tokenizer artifact does not match the pinned SHA-256")
        # The original GPT-2 artifact predates the explicit model.type field.
        # Inspect the current runtime's parsed model while retaining the exact pinned bytes.
        self.core = Tokenizer.from_str(self.payload.decode("utf-8"))
        definition = parse_json(self.core.to_str().encode("utf-8"))
        model = definition.get("model", {})
        pre = definition.get("pre_tokenizer", {})
        post = definition.get("post_processor")
        if (
            model.get("type") != "BPE" or model.get("dropout") not in (None, 0)
            or definition.get("normalizer") is not None
            or pre.get("type") != "ByteLevel" or pre.get("add_prefix_space") is not False
            or definition.get("decoder", {}).get("type") != "ByteLevel"
            or definition.get("truncation") is not None or definition.get("padding") is not None
            or (post is not None and post.get("type") != "ByteLevel")
        ):
            raise ValueError("requires lossless ByteLevel BPE without normalization, padding, or truncation")
        self.vocab_size = self.core.get_vocab_size(with_added_tokens=True)
        if not 256 <= self.vocab_size <= 1_000_000:
            raise ValueError("unsupported vocabulary size")
        self.descriptor = {
            "sha256": self.sha256, "library": "tokenizers", "version": version("tokenizers"),
            "vocab_size": self.vocab_size, "contract": "independent-utf8-chunks/no-added-special-tokens/v1",
        }

    def encode(self, text: str) -> list[int]:
        text.encode("utf-8", errors="strict")
        ids = self.core.encode(text, add_special_tokens=False).ids
        if self.core.decode(ids, skip_special_tokens=False) != text:
            raise ValueError("tokenizer is not lossless for this source chunk")
        return ids

    def validate_descriptor(self, descriptor: dict) -> None:
        if descriptor != self.descriptor:
            raise ValueError("tokenizer artifact, runtime version, or encoding contract mismatch")
