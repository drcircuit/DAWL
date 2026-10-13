"""Archive public platform evidence and produce an explicitly scoped engineering report."""
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, immutable_write, read_json, sha256, write_json
from dawl_platform.workflow import open_node, implementation_digest


def main():
    root = Path("artifacts/platform-v1")
    ledger, objects = open_node(root, read_only=True)
    output = Path("docs/results/platform-run-001")
    output.mkdir(parents=True, exist_ok=False)
    entries = list(ledger.entries())
    audits = [e for e in entries if e["body"]["kind"] == "audit"]
    vectors = [e for e in entries if e["body"]["kind"] == "vectors"]
    token_run = read_json(next(root.glob("token-run-*.json")))
    vector_run = read_json(next(root.glob("vector-run-*.json")))
    benchmark = read_json(root / "benchmark.json")
    deployment = read_json(Path("artifacts/platform-deployment-check-001/report.json"))
    public = read_json(next(Path("artifacts/platform-public-verifier-001").glob("public-replay-*.json")))
    tests = Path("artifacts/platform-tests-final.log").read_bytes()
    if not re.search(rb"Ran 73 tests in .*\r?\n\r?\nOK", tests):
        raise ValueError("expected final passing test record")
    if not all(e["body"]["payload"]["accepted"] for e in audits) or len(audits) != 8:
        raise ValueError("incomplete accepted corpus audits")
    if not deployment["accepted"] or not deployment["outage_exercised"] or not public["accepted"]:
        raise ValueError("deployment/public verification incomplete")
    ledger_bytes = b"\n".join(canonical_json(e) for e in entries) + b"\n"
    trust_bytes = canonical_json(ledger.trust)
    immutable_write(output / "ledger.jsonl", ledger_bytes)
    immutable_write(output / "trust.json", trust_bytes)
    immutable_write(output / "tests.log", tests)
    profiles = [p for p in objects.root.glob("directml-profile*.json")
                if sha256(p.read_bytes()) == vector_run["gpu_execution_profile"]["profile_sha256"]]
    if len(profiles) != 1:
        raise ValueError("missing exact GPU execution profile")
    immutable_write(output / "gpu-profile.json", profiles[0].read_bytes())
    evidence = {"audit-receipts.json": audits, "token-run.json": token_run, "vector-run.json": vector_run,
                "benchmark.json": benchmark, "deployment.json": deployment, "public-verification.json": public,
                "embedding.json": read_json(Path("artifacts/platform-assets/embedding.json"))}
    final_deployment = Path("artifacts/platform-deployment-final/report.json")
    if final_deployment.exists():
        evidence["final-deployment.json"] = read_json(final_deployment)
    for name, value in evidence.items():
        write_json(output / name, value)
    objects_index = [{"sha256": p.name, "bytes": p.stat().st_size} for p in sorted(objects.root.iterdir())
                     if re.fullmatch("[0-9a-f]{64}", p.name)]
    write_json(output / "artifact-index.json", objects_index)
    tokens = sum(e["body"]["payload"]["tokens"] for e in vectors)
    dense_bytes = sum(e["body"]["payload"]["dense_bytes_reconstructed"] for e in vectors)
    package_bytes = sum(d["bytes"] for d in objects_index) + len(ledger_bytes) + len(trust_bytes)
    dataset = next(e for e in entries if e["body"]["kind"] == "dataset")
    summary = {"schema": "dawl.platform-record/1", "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "capture_code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "current_implementation_sha256": implementation_digest(),
        "historical_stage_digests": sorted({e["body"]["payload"]["implementation_sha256"] for e in entries
                                            if "implementation_sha256" in e["body"]["payload"]}),
        "checkpoint": ledger.verify_history(), "dataset_entry": dataset["id"], "books": 8,
        "source_bytes": sum(e["body"]["payload"]["source_bytes"] for e in audits), "tokens": tokens,
        "checked_chunks": sum(e["body"]["payload"]["checked_chunks"] for e in audits),
        "dense_bytes_reconstructed": dense_bytes, "dense_sequence_bytes_persisted": 0,
        "token_payload_bytes": tokens * 4, "embedding_table_bytes": 154389504,
        "public_package_bytes": package_bytes, "package_reduction_vs_dense_percent": 100 * (1 - package_bytes / dense_bytes),
        "token_only_reduction_vs_dense_percent": 100 * (1 - tokens * 4 / dense_bytes),
        "container_audit_seconds_sum": sum(e["body"]["payload"]["seconds"] for e in audits),
        "tests_passed": 73, "public_replay_passed": True, "replica_outage_replay_passed": True,
        "evidence_hashes": {p.name: sha256(p.read_bytes()) for p in sorted(output.iterdir())},
        "limitations": ["Local federation; one host/operator; static jobs; explicit peer sync.",
                        "Eight historical English books, not internet-scale or heterogeneous-corpus evidence.",
                        "Input lookup vectors only; no contextual layers or model training.",
                        "Imported pretrained weights lack DAWL-verified pretraining lineage.",
                        "Initial stage and benchmark runs occurred during implementation; historical code fingerprints are recorded, not all are committed snapshots.",
                        "UI served and API tested; visual browser review was not performed."]}
    write_json(output / "summary.json", summary)
    table = "\n".join(f"| {r['workers']} | {r['median_seconds']:.2f} | {r['min_seconds']:.2f}–{r['max_seconds']:.2f} | {r['speedup']:.2f}× |" for r in benchmark["results"])
    report = f"""# DAWL platform execution record — 2026-10-13

The actual ingestion → token snapshot → input-vector reconstruction → independent
CPU audit path completed for all eight locked books. The research questionnaire is
not part of this platform. No model training took place.

## Executed result

| Measurement | Observed |
| --- | ---: |
| Original text | {summary['source_bytes']:,} bytes |
| Books / chunks | 8 / {summary['checked_chunks']:,} |
| Real GPT-2 tokens | {tokens:,} |
| GPU-reconstructed float32 input vectors | {dense_bytes:,} bytes |
| Dense token-sequence vector bytes written to disk | 0 |
| Token payload | {tokens * 4:,} bytes |
| Shared real GPT-2 embedding table | 154,389,504 bytes |
| Public artifact package, ledger export and bootstrap policy | {package_bytes:,} bytes |
| CPU container audit | Every source, token and vector chunk passed |
| Ledger | {len(entries)} signed records; converged replicas |
| Final automated suite | 73 tests passed |

The GPU runtime profile records **1,438 DirectML Gather executions**. Recovery and
hashing took {vector_run['seconds']:.2f} seconds after table/session initialization;
this includes snapshot integrity checks and output commitments. The CPU container's
per-book replay times sum to {summary['container_audit_seconds_sum']:.2f} seconds,
including its first network download of the shared table. These are different scopes,
so their ratio is not a GPU speedup measurement.

Input vectors are real pretrained lookup rows, not random fixtures, contextual
representations or generated answers. CPU re-tokenization checks all original source
bytes; independent NumPy lookup reproduces every GPU vector commitment exactly.

## Storage accounting

Token payload alone is {summary['token_only_reduction_vs_dense_percent']:.4f}% smaller
than storing a 768-dimensional float32 vector per token. Including the shared table,
all published source/artifact bytes, ledger export and public policy changes the
observed reduction to **{summary['package_reduction_vs_dense_percent']:.2f}%** for this corpus.
The matrix is a one-time dependency reusable across corpora. Tokens are not smaller
than this raw text. This comparison excludes development caches, the original full
548 MB pretrained checkpoint, redundant benchmark stores, profiling logs and software.
It is not a claim about total workspace disk usage or total network traffic.

## CPU ingestion scaling

Three real runs per worker count, interleaved with a fixed seed; each produces fresh
signed per-book snapshots and performs a complete-file consumer check. All **72 book
jobs** reproduced the exact baseline snapshot IDs.

| CPU worker processes | Median seconds | Observed range | Speedup |
| ---: | ---: | ---: | ---: |
{table}

The measured task includes worker startup, tokenization, hashing, signing, file
publication and consumer verification. It excludes GPU reconstruction, HTTP transfers
and the separate CPU audit. Inputs share one Windows host and disk; OS caches and
OneDrive behaviour are uncontrolled. This supports local parallel ingestion throughput
for this workload. It does not demonstrate arbitrary scale, internet-node efficiency
or resistance to churn. These are engineering measurements taken during integration,
not a preregistered performance study.

## Replication, restart and independent checks

- Separate producer and auditor databases converge on the same 36-entry content-set
  digest. Both container processes run as UID 1000, with read-only root filesystems.
- Container replacement preserves all eight audit receipts and ledger contents.
  Repeating the audit command reuses the eight completed immutable checks.
- A fresh public verifier repeats the entire corpus using no private key.
- With the original artifact peer stopped, another fresh verifier downloads from
  the replica and successfully repeats all eight books. The source peer then recovers
  with the same checkpoint. Alternate-peer selection is explicit, not automatic.
- Tests reject signature tampering, unadmitted hats, missing parents, author forks,
  truncated peer exports, artifact corruption, missing vector coverage and validly
  signed but numerically wrong vector commitments. Earlier adversarial tokenization
  tests remain in the passing suite. HTTP routes expose no producer private keys.

The web interface and static assets are served successfully; backend/API and JavaScript
syntax checks passed. Visual browser review remains unverified. The live observatory
is [localhost:8880](http://127.0.0.1:8880/).

## Reproduction and limits

Use the [runbook](platform.md). [Machine-readable evidence](results/platform-run-001/summary.json)
includes hashes for the [ledger](results/platform-run-001/ledger.jsonl),
[trust policy](results/platform-run-001/trust.json),
[audit receipts](results/platform-run-001/audit-receipts.json),
[benchmark trials](results/platform-run-001/benchmark.json),
[public replay](results/platform-run-001/public-verification.json),
[outage replay](results/platform-run-001/deployment.json), and test log.
Raw books and large model/token artifacts remain in the ignored local caches; their
hashes and public serving paths are recorded. Exporting ledger metadata alone does
not carry every large artifact needed for offline replay.

Capture code commit: `{summary['capture_code_commit']}`. Source, vector, benchmark and
initial container checks ran during implementation and carry their observed code
fingerprints. Those fingerprints are not all archived Git revisions. The final public
outage replay used the committed implementation. This distinction is retained instead
of assigning old measurements to a later code revision.

Dataset entry: `{dataset['id']}`. Ledger set digest:
`{summary['checkpoint']['set_sha256']}`. Independent policy pin:
`{summary['checkpoint']['policy']}`.

This is an operational local federation. Open discovery, distributed leases and
scheduling, independent operators, incremental admission, quotas, conflict governance,
larger heterogeneous workloads and training/weight lineage remain to be built. The
imported embedding checkpoint has a verifiable byte identity and extraction recipe;
its historical pretraining-data provenance is outside DAWL. Data lineage does not
establish answer correctness or a causal datapoint-to-answer relationship.
"""
    Path("docs/platform_run_001.md").write_text(report, encoding="utf-8", newline="\n")
    print({"report": "docs/platform_run_001.md", "public_package_bytes": package_bytes,
           "package_reduction_percent": summary["package_reduction_vs_dense_percent"]})


if __name__ == "__main__":
    main()
