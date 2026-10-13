"""Independent CPU replay: public HTTP inputs, separately pinned trust, own receipt key."""
from pathlib import Path
from time import perf_counter
import platform
import tempfile

from dawl_corpus.corpus import partition_book
from dawl_node.common import (canonical_json, immutable_write, load_key, parse_json, read_json,
                              sha256, unpack_ids, write_json)
from dawl_node.source import build_registry, load_registry, scan_source
from dawl_node.store import read_records, validate_manifest, verify_snapshot
from dawl_node.tokenizer import PinnedTokenizer
from .artifacts import Objects, PublicPeer, sync
from .ledger import Ledger
from .vectors import EmbeddingTable
from .workflow import implementation_digest, open_node


def linked(ledger, child, field, kind):
    ref = child["body"]["payload"][field]
    if ref not in child["body"]["parents"]:
        raise ValueError("artifact reference is not a signed lineage parent")
    parent = ledger.get(ref)
    if parent["body"]["kind"] != kind:
        raise ValueError("incorrect parent kind")
    return parent


def verify_book(ledger, objects, peer, vector_entry, table_cache, positions=None):
    started = perf_counter()
    vp = vector_entry["body"]["payload"]
    token_entry = linked(ledger, vector_entry, "tokens_entry", "tokens")
    table_entry = linked(ledger, vector_entry, "table_entry", "resource")
    source_entry = linked(ledger, token_entry, "source_entry", "source")
    tokenizer_entry = linked(ledger, token_entry, "tokenizer_entry", "resource")
    source, tp = source_entry["body"]["payload"], token_entry["body"]["payload"]
    table_resource, tokenizer_resource = table_entry["body"]["payload"], tokenizer_entry["body"]["payload"]
    if (table_resource["type"] != "embedding-table" or tokenizer_resource["type"] != "tokenizer"
            or tokenizer_entry["id"] not in table_entry["body"]["parents"]
            or not source["book_id"] == tp["book_id"] == vp["book_id"]):
        raise ValueError("lineage resources or book identity disagree")
    anchors = ledger.trust["anchors"]
    descriptor = table_resource["descriptor"]
    if (descriptor["sha256"] != anchors["embedding_sha256"]
            or descriptor["tokenizer_sha256"] != anchors["tokenizer_sha256"]
            or tokenizer_resource["descriptor"]["sha256"] != anchors["tokenizer_sha256"]
            or vp["contract"] != descriptor["contract"]):
        raise ValueError("transform differs from independently pinned inputs")

    def get(descriptor):
        objects.fetch(peer, descriptor)
        return objects.read(descriptor)

    if len(source_entry["body"]["parents"]) != 1:
        raise ValueError("source needs its corpus-lock parent")
    corpus_entry = ledger.get(source_entry["body"]["parents"][0])
    corpus = corpus_entry["body"]["payload"]
    if corpus_entry["body"]["kind"] != "resource" or corpus["type"] != "corpus-lock":
        raise ValueError("invalid corpus-lock parent")
    lock_bytes = get(corpus["files"]["lock"])
    if sha256(lock_bytes) != anchors["corpus_lock_sha256"]:
        raise ValueError("corpus lock differs from trust anchor")
    books = [b for b in parse_json(lock_bytes)["books"] if b["id"] == source["book_id"]]
    if len(books) != 1:
        raise ValueError("book absent or duplicated in corpus lock")
    book = books[0]
    if source["title"] != book["title"]:
        raise ValueError("source title differs from the corpus lock")
    book_bytes = get(source["files"]["book"])
    if sha256(book_bytes) != book["sha256"] or len(book_bytes) != book["bytes"]:
        raise ValueError("original source differs from corpus lock")
    divided = list(partition_book(book, book_bytes))
    if parse_json(get(source["files"]["parts"])) != [p for _, p in divided]:
        raise ValueError("part mapping does not reproduce the original book")
    registry = parse_json(get(source["files"]["registry"]))
    tokenizer_path = objects.fetch(peer, tokenizer_resource["files"]["tokenizer"])
    tokenizer = PinnedTokenizer(tokenizer_path, anchors["tokenizer_sha256"])
    if descriptor["shape"][0] != tokenizer.vocab_size:
        raise ValueError("embedding rows must match the independently pinned tokenizer vocabulary")
    if table_entry["id"] not in table_cache:
        table_path = objects.fetch(peer, table_resource["files"]["table"])
        table_cache[table_entry["id"]] = EmbeddingTable(table_path, descriptor, "cpu")
    table = table_cache[table_entry["id"]]
    vector_index = parse_json(get(vp["files"]["index"]))
    if not isinstance(vector_index, list) or len(vector_index) != len(registry["body"]["records"]):
        raise ValueError("vector coverage differs from source coverage")
    manifest = parse_json(get(tp["files"]["manifest.json"]))
    if manifest["id"] != tp["snapshot_id"]:
        raise ValueError("snapshot differs from ledger commitment")
    body = validate_manifest(manifest, registry, tokenizer, token_entry["body"]["signer"])
    if positions is not None and (not isinstance(positions, list) or not 1 <= len(positions) <= 256
            or positions != sorted(set(positions))
            or any(type(i) is not int or not 0 <= i < body["chunk_count"] for i in positions)):
        raise ValueError("invalid retained sample positions")
    selected = set(positions) if positions is not None else None
    if (tp["chunks"] != body["chunk_count"] or tp["tokens"] != body["token_count"]
            or vp["chunks"] != body["chunk_count"] or vp["tokens"] != body["token_count"]
            or source["registry_id"] != registry["id"] or source["chunks"] != body["chunk_count"]
            or vp["persisted_dense_vector_bytes"] != 0):
        raise ValueError("ledger summaries differ from committed artifacts")
    allowed = {"source.json", "tokenizer.json", "manifest.json"} | {
        f"{s['name']}.{suffix}" for s in body["shards"] for suffix in ("u32le", "records.json")}
    if set(tp["files"]) != allowed:
        raise ValueError("unexpected or missing snapshot files")
    checked, dense_bytes = 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        input_path = base / "input.jsonl"
        input_path.write_bytes(b"\n".join(canonical_json(d) for d, _ in divided))
        if build_registry(input_path, 4096) != registry:
            raise ValueError("registry does not reproduce the locked original book")
        directory = base / manifest["id"]
        directory.mkdir()
        for name in sorted(allowed):
            (directory / name).write_bytes(get(tp["files"][name]))
        verify_snapshot(directory, registry, tokenizer, token_entry["body"]["signer"])
        raw_chunks = {record["index"]: raw for kind, record, raw in scan_source(input_path, 4096) if kind == "chunk"}
        for shard in body["shards"]:
            payload = (directory / f"{shard['name']}.u32le").read_bytes()
            for record in read_records(directory, shard):
                index = record["source_index"]
                if selected is not None and index not in selected:
                    continue
                start, length = record["token_offset"] * 4, record["token_count"] * 4
                token_bytes = payload[start:start + length]
                ids = unpack_ids(token_bytes)
                if ids != tokenizer.encode(raw_chunks[index].decode("utf-8")):
                    raise ValueError(f"retokenization mismatch at chunk {index}")
                vector_bytes = table.payload(ids)
                expected = {"source_index": index, "tokens_sha256": sha256(token_bytes),
                            "vectors_sha256": sha256(vector_bytes), "vector_bytes": len(vector_bytes)}
                if vector_index[index] != expected:
                    raise ValueError(f"reconstructed vector mismatch at chunk {index}")
                dense_bytes += len(vector_bytes)
                checked += 1
    if checked != (len(selected) if selected is not None else body["chunk_count"]):
        raise ValueError("incomplete vector audit")
    if selected is None and dense_bytes != vp["dense_bytes_reconstructed"]:
        raise ValueError("incomplete vector byte count")
    return {"schema": "dawl.platform-audit/1", "book_id": book["id"], "accepted": True,
            "tokens_entry": token_entry["id"], "vectors_entry": vector_entry["id"],
            "source_entry": source_entry["id"], "table_entry": table_entry["id"],
            "checked_chunks": checked, "total_chunks": body["chunk_count"], "positions": positions,
            "tokens": body["token_count"], "source_bytes": len(book_bytes),
            "dense_bytes_reconstructed": dense_bytes, "comparison": "byte-exact SHA-256 for selected chunks" if selected is not None else "byte-exact SHA-256 for every chunk",
            "seconds": perf_counter() - started, "runtime": table.runtime,
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "implementation_sha256": implementation_digest(),
            "scope": "Original-book and snapshot integrity; sampled retokenization and input-embedding reproduction. No training claim." if selected is not None else "Full original-book reconstruction, retokenization and input-embedding reproduction. No training claim."}


def audit(root, peer_url, allow_insecure=False, key_path=None):
    ledger, objects = open_node(root)
    peer = PublicPeer(peer_url, allow_insecure)
    replication = sync(ledger, peer)
    key = load_key(key_path or root / "identities/auditor/key.private.pem")
    public = key.public_key().public_bytes_raw().hex()
    done = {e["body"]["payload"]["vectors_entry"] for e in ledger.entries()
            if e["body"]["kind"] == "audit" and e["body"]["signer"] == public and e["body"]["payload"]["accepted"]}
    table_cache, results = {}, []
    for entry in list(ledger.entries()):
        if entry["body"]["kind"] != "vectors" or entry["id"] in done:
            continue
        try:
            report = verify_book(ledger, objects, peer, entry, table_cache)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
            report = {"schema": "dawl.platform-audit/1", "accepted": False,
                      "book_id": entry["body"]["payload"].get("book_id"), "vectors_entry": entry["id"],
                      "error": str(error), "checked_chunks": 0, "tokens": 0}
        receipt = ledger.create("audit", {**report, "files": {"report": objects.json(report)}}, [entry["id"]], key)
        results.append(receipt)
        print(f"CPU audited book {report['book_id']}: accepted={report['accepted']}", flush=True)
    return {"replication": replication, "receipts": results, "reused": len(done),
            "accepted": bool(results or done) and all(e["body"]["payload"]["accepted"] for e in results)}


def verify_public(root, peer_url, allow_insecure=False):
    """Anyone can replay the artifacts with public pins, without an admitted key."""
    ledger, objects = open_node(root)
    peer = PublicPeer(peer_url, allow_insecure)
    replication = sync(ledger, peer)
    table_cache, results = {}, []
    for entry in list(ledger.entries()):
        if entry["body"]["kind"] != "vectors":
            continue
        try:
            result = verify_book(ledger, objects, peer, entry, table_cache)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
            result = {"accepted": False, "vectors_entry": entry["id"], "error": str(error)}
        results.append(result)
        print(f"Public replay {len(results)}: accepted={result['accepted']}", flush=True)
    report = {"schema": "dawl.public-replay/1", "accepted": bool(results) and all(r["accepted"] for r in results),
              "checkpoint": ledger.verify_history(), "replication": replication, "results": results,
              "private_keys_used": False, "scope": "Replayed all vector lineages in the downloaded checkpoint."}
    write_json(root / f"public-replay-{sha256(canonical_json(report))}.json", report)
    return report
