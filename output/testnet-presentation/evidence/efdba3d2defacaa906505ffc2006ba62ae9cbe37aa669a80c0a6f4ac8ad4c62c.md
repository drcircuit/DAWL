# o200k_base corpus run 001

The [machine-readable report](report.json) contains the signed local Blueprint,
profile, public trust policy, ledger checkpoint, per-book counts and smoke checks.

The hash-locked 13-book corpus contains 7,355,184 source bytes and 1,807 Frags.
Pinned `tiktoken==0.14.0` with `o200k_base` produced **1,753,309 tokens**, compared
with the historical GPT-2 count of 2,061,330: **14.9428% fewer**. Raw UTF-8 and
normalized text are identical under this encoding contract. Full local replay
accepted every book and the signed Blueprint; all nine smoke cases completed.

The profile is tokenizer-only, with no model configuration, embedding matrix or
claimed vector reconstruction. This run precedes the separate protocol-3 cluster
exercise; its Blueprint alone is not an Oracle certificate. It supplements the
five-profile [earlier comparison](../multiprofile-run-001/report.json) without
overwriting that evidence. Timing is an uncontrolled local pipeline measurement,
not a throughput benchmark.

Reproduce with a fresh root and report path:

```powershell
.\.venv\Scripts\python.exe scripts/fetch_o200k.py
.\.venv\Scripts\python.exe scripts/profile_corpus_pilot.py --profiles o200k_base --root artifacts/o200k-repeat --report docs/results/o200k-repeat/report.json
```
