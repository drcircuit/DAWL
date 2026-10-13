"""Interleaved real ingestion scaling runs; same books, keys and snapshot IDs each run."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import random
import statistics
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import load_public, read_json, write_json
from dawl_node.source import load_registry
from dawl_node.store import mine, verify_snapshot
from dawl_node.tokenizer import PinnedTokenizer
from dawl_platform.workflow import open_node, implementation_digest


def job(spec):
    root, output, book, expected = spec
    root, output = Path(root), Path(output)
    directory = root / "jobs" / str(book)
    config = read_json(root / "config.json")
    ledger, objects = open_node(root, read_only=True)
    token = ledger.get(config["tokenizer_entry"])["body"]["payload"]
    tokenizer = PinnedTokenizer(objects.root / token["files"]["tokenizer"]["sha256"], token["descriptor"]["sha256"])
    key_path = root / f"identities/tokenizer-{book}/key.private.pem"
    run = mine(directory / "input.jsonl", directory / "source.json", tokenizer, key_path, output, 65536)
    if run["snapshot_id"] != expected:
        raise ValueError("worker count changed the committed output")
    check = verify_snapshot(output / "snapshots" / expected, load_registry(directory / "source.json"),
                            tokenizer, load_public(key_path.parent / "public.key"))
    return {"book_id": book, "pid": os.getpid(), **run, "consumer_check": check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/platform-v1"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/platform-benchmark-001"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ledger, _ = open_node(args.root, read_only=True)
    expected = {e["body"]["payload"]["book_id"]: e["body"]["payload"]["snapshot_id"]
                for e in ledger.entries() if e["body"]["kind"] == "tokens"}
    if len(expected) != len(read_json(args.root / "config.json")["jobs"]):
        raise ValueError("benchmark needs a complete baseline")
    rng, trials = random.Random(20261013), []
    for repeat in range(3):
        order = [1, 2, 4]
        rng.shuffle(order)
        for workers in order:
            trial_dir = args.output / f"repeat-{repeat}-workers-{workers}"
            trial_dir.mkdir()
            specs = [(str(args.root.resolve()), str((trial_dir / str(book)).resolve()), book, snapshot)
                     for book, snapshot in expected.items()]
            started = perf_counter()
            with ProcessPoolExecutor(max_workers=workers) as pool:
                jobs = list(pool.map(job, specs))
            elapsed = perf_counter() - started
            trial = {"repeat": repeat, "workers": workers, "books": len(jobs), "seconds": elapsed, "jobs": jobs,
                     "tokens_per_second": sum(j["tokens"] for j in jobs) / elapsed}
            write_json(trial_dir / "trial.json", trial)
            trials.append(trial)
            print(f"Repeat {repeat + 1}, {workers} workers: {elapsed:.3f}s", flush=True)
    baseline = statistics.median(t["seconds"] for t in trials if t["workers"] == 1)
    rows = []
    for workers in (1, 2, 4):
        seconds = [t["seconds"] for t in trials if t["workers"] == workers]
        median = statistics.median(seconds)
        rows.append({"workers": workers, "books": len(expected), "median_seconds": median,
                     "min_seconds": min(seconds), "max_seconds": max(seconds), "speedup": baseline / median})
    report = {"schema": "dawl.ingestion-scaling/1", "results": rows, "trials": trials,
              "seed": 20261013, "recorded_utc": datetime.now(timezone.utc).isoformat(),
              "implementation_sha256": implementation_digest(),
              "environment": {"platform": platform.platform(), "python": platform.python_version(), "logical_cpus": os.cpu_count()},
              "scope": "Actual book registration inputs -> signed token snapshots and consumer integrity checks. Local worker processes; same host/disk. No GPU/vector work or network transfer in this timing. Fresh output stores; OS caches uncontrolled. Pool startup included."}
    write_json(args.output / "summary.json", report)
    write_json(args.root / "benchmark.json", report)
    print(rows)


if __name__ == "__main__":
    main()
