"""Tokenizer/miner and storage hats: immutable signed snapshots and bounded shards."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from data_ingestion.protocol import MerkleTree, canonical_json, require_digest, sha256
from .common import (immutable_write, load_key, pack_ids, read_bytes, read_json, sign, staging_directory,
                     unpack_ids, verify_signature, write_json)
from .source import load_registry, scan_source
from .tokenizer import PinnedTokenizer

MAX_SHARD_TOKENS = 262144
MAX_SHARD_CHUNKS = 256


def validate_manifest(envelope: dict, registry: dict, tokenizer: PinnedTokenizer, public: str) -> dict:
    body = verify_signature(envelope, public)
    try:
        if set(body) != {"schema", "signer", "source_registry", "tokenizer", "chunk_count", "token_count", "shards"}:
            raise ValueError("invalid snapshot fields")
        if body["schema"] != "dawl.snapshot/v2" or body["source_registry"] != registry["id"]:
            raise ValueError("snapshot schema or source commitment mismatch")
        tokenizer.validate_descriptor(body["tokenizer"])
        if type(body["chunk_count"]) is not int or body["chunk_count"] != len(registry["body"]["records"]):
            raise ValueError("snapshot does not cover the registered source")
        if not isinstance(body["shards"], list) or not 1 <= len(body["shards"]) <= body["chunk_count"]:
            raise ValueError("invalid shard list")
        next_chunk, tokens = 0, 0
        for number, shard in enumerate(body["shards"]):
            if set(shard) != {"name", "first_chunk", "chunk_count", "token_count", "tokens_sha256", "records_sha256", "root"}:
                raise ValueError("invalid shard fields")
            if shard["name"] != f"{number:06d}" or type(shard["first_chunk"]) is not int or shard["first_chunk"] != next_chunk:
                raise ValueError("invalid shard ordering or name")
            if type(shard["chunk_count"]) is not int or not 1 <= shard["chunk_count"] <= MAX_SHARD_CHUNKS:
                raise ValueError("invalid shard chunk count")
            if type(shard["token_count"]) is not int or not 1 <= shard["token_count"] <= MAX_SHARD_TOKENS:
                raise ValueError("invalid shard token count")
            for key in ("tokens_sha256", "records_sha256", "root"):
                require_digest(shard[key])
            next_chunk += shard["chunk_count"]
            tokens += shard["token_count"]
        if next_chunk != body["chunk_count"] or type(body["token_count"]) is not int or tokens != body["token_count"]:
            raise ValueError("inconsistent snapshot totals")
        return body
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError("invalid snapshot manifest") from error


def read_records(directory: Path, shard: dict) -> list[dict]:
    payload = read_bytes(directory / f"{shard['name']}.records.json", 128 * 1024)
    if sha256(payload) != shard["records_sha256"]:
        raise ValueError("shard record file hash mismatch")
    from .common import parse_json
    records = parse_json(payload)
    if not isinstance(records, list) or len(records) != shard["chunk_count"]:
        raise ValueError("shard record count mismatch")
    offset = 0
    for local_index, record in enumerate(records):
        if set(record) != {"source_index", "token_offset", "token_count", "sha256"}:
            raise ValueError("invalid token record fields")
        if (type(record["source_index"]) is not int or record["source_index"] != shard["first_chunk"] + local_index
                or type(record["token_offset"]) is not int or record["token_offset"] != offset
                or type(record["token_count"]) is not int or not 1 <= record["token_count"] <= 16384):
            raise ValueError("invalid token record index, offset, or length")
        require_digest(record["sha256"])
        offset += record["token_count"]
    if offset != shard["token_count"] or MerkleTree(b"token", map(canonical_json, records)).root != shard["root"]:
        raise ValueError("token record root or total mismatch")
    return records


def mine(input_path: Path, registry_path: Path, tokenizer: PinnedTokenizer,
         key_path: Path, root: Path, shard_tokens: int = 131072) -> dict:
    if type(shard_tokens) is not int or not 16384 <= shard_tokens <= MAX_SHARD_TOKENS:
        raise ValueError("shard token limit must be in [16384, 262144]")
    start = perf_counter()
    registry = load_registry(registry_path)
    expected = registry["body"]["records"]
    root.mkdir(parents=True, exist_ok=True)
    (root / ".staging").mkdir(exist_ok=True)
    (root / "snapshots").mkdir(exist_ok=True)
    shards, docs, records = [], [], []
    buffer = bytearray()
    next_source, total_tokens = 0, 0
    with staging_directory(root / ".staging", "mine-") as directory:
        def flush():
            if not records:
                return
            name = f"{len(shards):06d}"
            token_payload = bytes(buffer)
            record_payload = canonical_json(records)
            shards.append({"name": name, "first_chunk": records[0]["source_index"],
                           "chunk_count": len(records), "token_count": len(buffer) // 4,
                           "tokens_sha256": sha256(token_payload), "records_sha256": sha256(record_payload),
                           "root": MerkleTree(b"token", map(canonical_json, records)).root})
            (directory / f"{name}.u32le").write_bytes(token_payload)
            (directory / f"{name}.records.json").write_bytes(record_payload)
            records.clear()
            buffer.clear()
        for kind, record, raw in scan_source(input_path, registry["body"]["chunk_bytes"]):
            if kind == "document":
                docs.append(record)
                continue
            if next_source >= len(expected) or record != expected[next_source]:
                raise ValueError("input does not match the independently registered source")
            ids = tokenizer.encode(raw.decode("utf-8"))
            if not ids or len(ids) > 16384:
                raise ValueError("tokenized chunk exceeds the supported size")
            if len(buffer) // 4 + len(ids) > shard_tokens or len(records) == MAX_SHARD_CHUNKS:
                flush()
            token_payload = pack_ids(ids)
            records.append({"source_index": next_source, "token_offset": len(buffer) // 4,
                            "token_count": len(ids), "sha256": sha256(token_payload)})
            buffer.extend(token_payload)
            immutable_write(root / "objects" / record["sha256"], raw)
            total_tokens += len(ids)
            next_source += 1
        if next_source != len(expected) or docs != registry["body"]["documents"]:
            raise ValueError("input is incomplete or source metadata changed")
        flush()
        envelope = sign({"schema": "dawl.snapshot/v2", "source_registry": registry["id"],
                         "tokenizer": tokenizer.descriptor, "chunk_count": next_source,
                         "token_count": total_tokens, "shards": shards}, load_key(key_path))
        (directory / "manifest.json").write_bytes(canonical_json(envelope))
        (directory / "source.json").write_bytes(canonical_json(registry))
        (directory / "tokenizer.json").write_bytes(tokenizer.payload)
        destination = root / "snapshots" / envelope["id"]
        # Publish the complete directory in one rename. Staging is never served.
        if destination.exists():
            verify_snapshot(destination, registry, tokenizer, envelope["body"]["signer"])
        else:
            try:
                directory.rename(destination)
            except FileExistsError:
                verify_snapshot(destination, registry, tokenizer, envelope["body"]["signer"])
    return {"snapshot_id": envelope["id"], "registry_id": registry["id"], "chunks": next_source,
            "tokens": total_tokens, "shards": len(shards), "seconds": perf_counter() - start,
            "token_payload_bytes": total_tokens * 4,
            "published_bytes": sum(path.stat().st_size for path in destination.iterdir()),
            "source_bytes_unique_chunks": sum(record["byte_length"] for record in expected)}


def verify_snapshot(directory: Path, registry: dict, tokenizer: PinnedTokenizer, public: str) -> dict:
    envelope = read_json(directory / "manifest.json")
    if directory.name != envelope["id"]:
        raise ValueError("snapshot directory ID mismatch")
    body = validate_manifest(envelope, registry, tokenizer, public)
    if read_json(directory / "source.json") != registry or read_bytes(directory / "tokenizer.json") != tokenizer.payload:
        raise ValueError("snapshot source or tokenizer artifact mismatch")
    count = 0
    for shard in body["shards"]:
        records = read_records(directory, shard)
        payload = read_bytes(directory / f"{shard['name']}.u32le", MAX_SHARD_TOKENS * 4)
        if len(payload) != shard["token_count"] * 4 or sha256(payload) != shard["tokens_sha256"]:
            raise ValueError("token shard payload is corrupt")
        for record in records:
            start = record["token_offset"] * 4
            chunk = payload[start:start + record["token_count"] * 4]
            if sha256(chunk) != record["sha256"] or any(token >= tokenizer.vocab_size for token in unpack_ids(chunk)):
                raise ValueError("token chunk hash or vocabulary range mismatch")
            count += 1
    return {"snapshot_id": envelope["id"], "verified_chunks": count, "verified_tokens": body["token_count"],
            "verification": "complete-file integrity; sampled source consistency is a separate audit"}


class StoredSnapshot:
    """Caches authenticated metadata; reads requested source and token ranges from disk."""

    def __init__(self, root: Path, snapshot_id: str):
        require_digest(snapshot_id)
        self.root, self.directory = root, root / "snapshots" / snapshot_id
        self.envelope = read_json(self.directory / "manifest.json")
        if self.envelope["id"] != snapshot_id:
            raise ValueError("snapshot ID mismatch")
        self.registry = load_registry(self.directory / "source.json")
        self.tokenizer = PinnedTokenizer(self.directory / "tokenizer.json", self.envelope["body"]["tokenizer"]["sha256"])
        self.body = validate_manifest(self.envelope, self.registry, self.tokenizer, self.envelope["body"]["signer"])
        self.source_tree = MerkleTree(b"source", map(canonical_json, self.registry["body"]["records"]))
        self.shards = {}
        self.by_source = {}
        for shard in self.body["shards"]:
            records = read_records(self.directory, shard)
            tree = MerkleTree(b"token", map(canonical_json, records))
            self.shards[shard["name"]] = shard
            for index, record in enumerate(records):
                self.by_source[record["source_index"]] = (shard, record, index, tree)

    def respond(self, nonce: str, positions: list[int]) -> dict:
        require_digest(nonce)
        if not positions or len(positions) > 256 or len(set(positions)) != len(positions):
            raise ValueError("challenge needs 1..256 distinct source chunk positions")
        if any(type(index) is not int or index not in self.by_source for index in positions):
            raise ValueError("challenge position out of range")
        samples = []
        for index in positions:
            source = self.registry["body"]["records"][index]
            shard, record, local_index, tree = self.by_source[index]
            raw = read_bytes(self.root / "objects" / source["sha256"], 16384)
            with (self.directory / f"{shard['name']}.u32le").open("rb") as stream:
                stream.seek(record["token_offset"] * 4)
                tokens = stream.read(record["token_count"] * 4)
            samples.append({"source": source, "raw_text": raw.decode("utf-8"),
                            "source_proof": list(self.source_tree.proof(index)),
                            "shard": shard["name"], "record": record,
                            "token_ids": unpack_ids(tokens), "token_proof": list(tree.proof(local_index))})
        return {"snapshot_id": self.envelope["id"], "nonce": nonce, "samples": samples}
