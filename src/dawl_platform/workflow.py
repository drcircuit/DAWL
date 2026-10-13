"""Restartable platform stages; immutable outputs become signed lineage entries."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter
import os

from dawl_corpus.corpus import partition_book
from dawl_node.common import (canonical_json, immutable_write, keygen, load_key, load_public,
                              read_bytes, read_json, sha256, sign, unpack_ids, write_json)
from dawl_node.source import load_registry, register
from dawl_node.store import mine, read_records, verify_snapshot
from dawl_node.tokenizer import PinnedTokenizer
from .artifacts import Objects
from .ledger import Ledger
from .vectors import EmbeddingTable


def open_node(root, read_only=False):
    root = Path(root)
    trust = read_json(root / "trust.json")
    return Ledger(root / "ledger.sqlite", trust, read_only), Objects(root / "objects")


def implementation_digest():
    root = Path(__file__).resolve().parents[1]
    paths = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return sha256(canonical_json({p.relative_to(root).as_posix(): sha256(p.read_bytes()) for p in paths}))


def prepare(root, raw, lock_path, assets, embedding_assets):
    root, raw = Path(root), Path(raw)
    root.mkdir(parents=True, exist_ok=False)
    lock = read_json(lock_path)
    embedding = read_json(embedding_assets / "embedding.json")
    roles = {"source": ["source"], "vector": ["vector"], "auditor": ["auditor"]}
    roles.update({f"tokenizer-{book['id']}": ["tokenizer"] for book in lock["books"]})
    identities, names = {}, {}
    for name, hats in roles.items():
        directory = root / "identities" / name
        public = keygen(directory / "key.private.pem", directory / "public.key")
        identities[public], names[public] = hats, name
    trust = {"schema": "dawl.trust/1", "identities": identities, "names": names,
             "anchors": {"embedding_sha256": embedding["sha256"], "tokenizer_sha256": embedding["tokenizer_sha256"],
                         "corpus_lock_sha256": sha256(canonical_json(lock))},
             "scope": "Explicit local federation admission; identities do not establish unique people."}
    write_json(root / "trust.json", trust)
    ledger, objects = open_node(root)
    key = load_key(root / "identities/source/key.private.pem")
    tokenizer = PinnedTokenizer(assets / "tokenizer.json", embedding["tokenizer_sha256"])
    table = objects.put(read_bytes(embedding_assets / "embedding.f32le", 256 * 1024 * 1024))
    if table["sha256"] != embedding["sha256"]:
        raise ValueError("embedding table differs from manifest")
    corpus_entry = ledger.create("resource", {"type": "corpus-lock", "files": {"lock": objects.json(lock)}}, [], key)
    tokenizer_entry = ledger.create("resource", {"type": "tokenizer", "descriptor": tokenizer.descriptor,
                                      "files": {"tokenizer": objects.put(tokenizer.payload)}}, [], key)
    table_entry = ledger.create("resource", {"type": "embedding-table", "descriptor": embedding,
                    "files": {"table": table, "model_card": objects.put((embedding_assets / "upstream-model-card.md").read_bytes())}},
                    [tokenizer_entry["id"]], key)
    jobs = []
    for book in lock["books"]:
        payload = read_bytes(raw / str(book["id"]) / f"pg{book['id']}.txt", 8 * 1024 * 1024)
        if len(payload) != book["bytes"] or sha256(payload) != book["sha256"]:
            raise ValueError("source book differs from corpus lock")
        divided = list(partition_book(book, payload))
        job = root / "jobs" / str(book["id"])
        job.mkdir(parents=True)
        input_path = job / "input.jsonl"
        immutable_write(input_path, b"\n".join(canonical_json(doc) for doc, _ in divided))
        register(input_path, job / "source.json", 4096)
        registry = load_registry(job / "source.json")
        source_entry = ledger.create("source", {"book_id": book["id"], "title": book["title"],
            "registry_id": registry["id"], "chunks": len(registry["body"]["records"]),
            "files": {"book": objects.put(payload), "parts": objects.json([part for _, part in divided]),
                      "registry": objects.json(registry)}}, [corpus_entry["id"]], key)
        jobs.append({"book_id": book["id"], "source_entry": source_entry["id"]})
    config = {"schema": "dawl.platform-lab/1", "jobs": jobs, "tokenizer_entry": tokenizer_entry["id"],
              "table_entry": table_entry["id"], "corpus_entry": corpus_entry["id"],
              "implementation_sha256": implementation_digest()}
    write_json(root / "config.json", config)
    return {"books": len(jobs), **ledger.checkpoint()}


def token_job(spec):
    root, job_info, config = Path(spec[0]), spec[1], spec[2]
    ledger, objects = open_node(root)
    book_id = job_info["book_id"]
    directory = root / "jobs" / str(book_id)
    token_resource = ledger.get(config["tokenizer_entry"])["body"]["payload"]
    tokenizer_path = objects.root / token_resource["files"]["tokenizer"]["sha256"]
    tokenizer = PinnedTokenizer(tokenizer_path, token_resource["descriptor"]["sha256"])
    key_path = root / f"identities/tokenizer-{book_id}/key.private.pem"
    result = mine(directory / "input.jsonl", directory / "source.json", tokenizer, key_path, directory / "store", 65536)
    snapshot = directory / "store/snapshots" / result["snapshot_id"]
    files = {p.name: objects.put(p.read_bytes()) for p in sorted(snapshot.iterdir())}
    body = {"schema": "dawl.lineage/1", "policy": ledger.policy, "sequence": 0, "previous": None,
            "kind": "tokens", "parents": sorted([job_info["source_entry"], config["tokenizer_entry"]]),
            "payload": {"book_id": book_id, "source_entry": job_info["source_entry"],
                        "tokenizer_entry": config["tokenizer_entry"], "snapshot_id": result["snapshot_id"],
                        "files": files, "chunks": result["chunks"], "tokens": result["tokens"],
                        "implementation_sha256": implementation_digest()}}
    envelope = sign(body, load_key(key_path))
    return {"entry": envelope, "timing": {"book_id": book_id, "process_id": os.getpid(), **result}}


def tokenize(root, workers=2):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    ledger, _ = open_node(root)
    config = read_json(root / "config.json")
    existing = {e["body"]["payload"]["book_id"] for e in ledger.entries() if e["body"]["kind"] == "tokens"}
    specs = [(str(root.resolve()), j, config) for j in config["jobs"] if j["book_id"] not in existing]
    start = perf_counter()
    results = []
    if specs:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(token_job, specs):
                ledger.add(result["entry"])
                results.append(result["timing"])
    report = {"workers": workers, "jobs_completed": len(results), "jobs_reused": len(existing),
              "seconds": perf_counter() - start, "jobs": results}
    if specs:
        write_json(root / f"token-run-{sha256(canonical_json(report))}.json", report)
    return report


def reconstruct(root, provider="cpu"):
    ledger, objects = open_node(root)
    config = read_json(root / "config.json")
    resource = ledger.get(config["table_entry"])["body"]["payload"]
    table = EmbeddingTable(objects.root / resource["files"]["table"]["sha256"], resource["descriptor"], provider)
    key = load_key(root / "identities/vector/key.private.pem")
    previous = {e["body"]["payload"]["tokens_entry"] for e in ledger.entries() if e["body"]["kind"] == "vectors"}
    reports = []
    start = perf_counter()
    for entry in list(ledger.entries()):
        if entry["body"]["kind"] != "tokens" or entry["id"] in previous:
            continue
        body = entry["body"]["payload"]
        directory = root / "jobs" / str(body["book_id"]) / "store/snapshots" / body["snapshot_id"]
        registry = load_registry(directory / "source.json")
        tokenizer = PinnedTokenizer(directory / "tokenizer.json", resource["descriptor"]["tokenizer_sha256"])
        verify_snapshot(directory, registry, tokenizer, entry["body"]["signer"])
        manifest = read_json(directory / "manifest.json")
        records, dense_bytes = [], 0
        book_started = perf_counter()
        for shard in manifest["body"]["shards"]:
            payload = (directory / f"{shard['name']}.u32le").read_bytes()
            for record in read_records(directory, shard):
                offset, length = record["token_offset"] * 4, record["token_count"] * 4
                token_bytes = payload[offset:offset + length]
                vectors = table.payload(unpack_ids(token_bytes))
                dense_bytes += len(vectors)
                records.append({"source_index": record["source_index"], "tokens_sha256": sha256(token_bytes),
                                "vectors_sha256": sha256(vectors), "vector_bytes": len(vectors)})
        vector_payload = {"book_id": body["book_id"], "tokens_entry": entry["id"], "table_entry": config["table_entry"],
                "contract": resource["descriptor"]["contract"], "files": {"index": objects.json(records)},
                "chunks": len(records), "tokens": body["tokens"], "dense_bytes_reconstructed": dense_bytes,
                "persisted_dense_vector_bytes": 0, "runtime": table.runtime,
                "implementation_sha256": implementation_digest()}
        result = ledger.create("vectors", vector_payload, [entry["id"], config["table_entry"]], key)
        reports.append({"book_id": body["book_id"], "entry": result["id"], "seconds": perf_counter() - book_started,
                        "dense_bytes": dense_bytes})
        print(f"Reconstructed book {body['book_id']}: {dense_bytes:,} vector bytes", flush=True)
    profile = table.finish_profile() if reports else None
    report = {"provider": provider, "books": len(reports), "seconds": perf_counter() - start,
              "jobs": reports, "gpu_execution_profile": profile}
    if reports:
        write_json(root / f"vector-run-{sha256(canonical_json(report))}.json", report)
    return report


def assemble(root):
    ledger, _ = open_node(root)
    config = read_json(root / "config.json")
    existing = [e for e in ledger.entries() if e["body"]["kind"] == "dataset"]
    if existing:
        return existing[-1]
    audits = {e["body"]["payload"]["book_id"]: e for e in ledger.entries()
              if e["body"]["kind"] == "audit" and e["body"]["payload"]["accepted"]}
    expected = {j["book_id"] for j in config["jobs"]}
    if set(audits) != expected:
        raise ValueError("every selected book needs an accepted auditor receipt before assembly")
    return ledger.create("dataset", {"corpus_entry": config["corpus_entry"], "books": sorted(expected),
        "tokens": sum(e["body"]["payload"]["tokens"] for e in audits.values()),
        "chunks": sum(e["body"]["payload"]["checked_chunks"] for e in audits.values()),
        "scope": "Audited source, token and input-embedding lineage. No model trained."},
        [e["id"] for e in audits.values()] + [config["corpus_entry"]], load_key(root / "identities/source/key.private.pem"))
