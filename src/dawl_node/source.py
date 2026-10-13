"""Source-provider role: bounded JSONL documents, provenance, and a pinned registry."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from data_ingestion.protocol import MerkleTree, canonical_json, sha256, require_digest
from .common import parse_json, read_json, write_json

MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_LINE_BYTES = 8 * MAX_DOCUMENT_BYTES
MAX_CHUNKS = 100_000
MAX_DOCUMENTS = 10_000


def documents(path: Path) -> Iterator[dict]:
    with path.open("rb") as stream:
        line_number = 0
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            line_number += 1
            if len(line) > MAX_LINE_BYTES:
                raise ValueError(f"JSONL line {line_number} exceeds the size limit")
            if not line.strip():
                continue
            doc = parse_json(line, MAX_LINE_BYTES)
            if not isinstance(doc, dict) or set(doc) != {"id", "text", "source_uri", "license"}:
                raise ValueError(f"line {line_number}: expected id, text, source_uri, license")
            for field in ("id", "source_uri", "license"):
                if not isinstance(doc[field], str) or not 1 <= len(doc[field]) <= 1024:
                    raise ValueError(f"invalid document {field}")
            if not isinstance(doc["text"], str):
                raise ValueError("document text must be a string")
            raw = doc["text"].encode("utf-8", errors="strict")
            if not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
                raise ValueError("document must contain 1..1048576 UTF-8 bytes")
            yield doc


def chunks(raw: bytes, byte_limit: int):
    if type(byte_limit) is not int or not 256 <= byte_limit <= 16384:
        raise ValueError("chunk byte limit must be in [256, 16384]")
    raw.decode("utf-8", errors="strict")
    start = 0
    while start < len(raw):
        end = min(start + byte_limit, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        yield start, raw[start:end]
        start = end


def scan_source(path: Path, chunk_bytes: int = 4096):
    """Stream document/chunk bytes; retain only bounded metadata and dedup hashes."""
    seen_ids, seen_hashes = set(), {}
    index = 0
    for doc in documents(path):
        if doc["id"] in seen_ids:
            raise ValueError(f"duplicate document ID: {doc['id']}")
        seen_ids.add(doc["id"])
        if len(seen_ids) > MAX_DOCUMENTS:
            raise ValueError("source exceeds the document limit")
        raw = doc["text"].encode("utf-8")
        digest = sha256(raw)
        metadata = {k: doc[k] for k in ("id", "source_uri", "license")}
        metadata.update({"sha256": digest, "utf8_bytes": len(raw),
                         "canonical_document": seen_hashes.get(digest, doc["id"])})
        yield "document", metadata, None
        if digest in seen_hashes:
            continue
        seen_hashes[digest] = doc["id"]
        for offset, payload in chunks(raw, chunk_bytes):
            if index >= MAX_CHUNKS:
                raise ValueError("source registry exceeds the chunk limit")
            record = {"index": index, "document_id": doc["id"], "document_sha256": digest,
                      "byte_offset": offset, "byte_length": len(payload), "sha256": sha256(payload)}
            yield "chunk", record, payload
            index += 1


def build_registry(path: Path, chunk_bytes: int = 4096) -> dict:
    docs, records = [], []
    for kind, record, _ in scan_source(path, chunk_bytes):
        (docs if kind == "document" else records).append(record)
    if not records:
        raise ValueError("source contains no documents")
    body = {"schema": "dawl.source/v2", "chunk_bytes": chunk_bytes,
            "documents": docs, "records": records,
            "root": MerkleTree(b"source", map(canonical_json, records)).root}
    encoded = canonical_json(body)
    if len(encoded) > 32 * 1024 * 1024 - 128:
        raise ValueError("registry metadata exceeds 32 MiB")
    return {"id": sha256(encoded), "body": body}


def load_registry(path: Path, expected_id: str | None = None) -> dict:
    registry = read_json(path)
    try:
        body = registry["body"]
        if set(registry) != {"id", "body"} or set(body) != {"schema", "chunk_bytes", "documents", "records", "root"}:
            raise ValueError("invalid source registry fields")
        if (body["schema"] != "dawl.source/v2" or type(body["chunk_bytes"]) is not int
                or not 256 <= body["chunk_bytes"] <= 16384):
            raise ValueError("invalid source registry version or chunk limit")
        if registry["id"] != sha256(canonical_json(body)) or (expected_id and registry["id"] != expected_id):
            raise ValueError("source registry commitment mismatch")
        records = body["records"]
        docs = body["documents"]
        if not isinstance(docs, list) or not 1 <= len(docs) <= MAX_DOCUMENTS:
            raise ValueError("invalid document list")
        doc_map, hashes, offsets = {}, {}, {}
        for doc in docs:
            if set(doc) != {"id", "source_uri", "license", "sha256", "utf8_bytes", "canonical_document"}:
                raise ValueError("invalid registered document fields")
            for field in ("id", "source_uri", "license", "canonical_document"):
                if not isinstance(doc[field], str) or not 1 <= len(doc[field]) <= 1024:
                    raise ValueError("invalid registered provenance")
            if doc["id"] in doc_map:
                raise ValueError("duplicate document identity")
            require_digest(doc["sha256"])
            if type(doc["utf8_bytes"]) is not int or not 1 <= doc["utf8_bytes"] <= MAX_DOCUMENT_BYTES:
                raise ValueError("invalid document byte count")
            canonical = hashes.setdefault(doc["sha256"], doc["id"])
            if doc["canonical_document"] != canonical:
                raise ValueError("invalid duplicate-document alias")
            doc_map[doc["id"]] = doc
            if canonical == doc["id"]:
                offsets[doc["id"]] = 0
        if not 1 <= len(records) <= MAX_CHUNKS:
            raise ValueError("invalid source chunk count")
        for index, record in enumerate(records):
            if set(record) != {"index", "document_id", "document_sha256", "byte_offset", "byte_length", "sha256"}:
                raise ValueError("invalid source chunk fields")
            if type(record["index"]) is not int or record["index"] != index:
                raise ValueError("source chunk indexes must be contiguous")
            if type(record["byte_offset"]) is not int or record["byte_offset"] < 0:
                raise ValueError("invalid source offset")
            if type(record["byte_length"]) is not int or not 1 <= record["byte_length"] <= body["chunk_bytes"]:
                raise ValueError("invalid source chunk length")
            require_digest(record["sha256"])
            require_digest(record["document_sha256"])
            doc = doc_map[record["document_id"]]
            if (doc["canonical_document"] != doc["id"] or doc["sha256"] != record["document_sha256"]
                    or record["byte_offset"] != offsets[doc["id"]]):
                raise ValueError("chunk does not continue its committed document")
            offsets[doc["id"]] += record["byte_length"]
        if any(offset != doc_map[doc_id]["utf8_bytes"] for doc_id, offset in offsets.items()):
            raise ValueError("source chunks do not cover complete documents")
        if body["root"] != MerkleTree(b"source", map(canonical_json, records)).root:
            raise ValueError("source root mismatch")
        return registry
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("invalid source registry") from error


def register(path: Path, output: Path, chunk_bytes: int = 4096) -> dict:
    registry = build_registry(path, chunk_bytes)
    write_json(output, registry)
    return {"registry_id": registry["id"], "documents": len(registry["body"]["documents"]),
            "unique_chunks": len(registry["body"]["records"])}
