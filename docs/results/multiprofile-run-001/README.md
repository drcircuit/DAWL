# Multi-profile local pilot 001

Executed 14 October 2026, 02:50:54-03:04:46 Oslo (UTC+2).

`report.json` records five independent signed token Blueprints over the same
13 hash-locked books, with 1,807 Frags each. All five passed full retokenization
and lineage replay. A cross-profile Blueprint was rejected. Raw book storage is
shared: 13 objects serve 65 references across profiles.

The run used GPT-2, SmolLM2, Qwen3, Qwen3.5 and SmolLM3 tokenizer/config artifacts.
Upstream revision pins, acquisition hashes, runtime versions, profile definitions,
public signing policy, Blueprint signatures and ledger checkpoints are included.
The report includes no private signing keys, full book texts or model weights.

This is a local tokenization/provenance experiment, not an Oracle-certified
release, vector benchmark, executed training run or validation of candidate model
weights. Input-table sizes are projections. Each profile uses the same local
operator key under a different profile-bound policy; there are not five operators.

The nine smoke inputs per profile cover byte-preservation and normalization
edges. They are not representative language-quality samples. Corpus token counts
are comparable for these exact raw book bytes and fixed source boundaries; they
are not a general model ranking.

All 38 focused and regression tests passed:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_profiles.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_ingestion_nodes.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_platform.py -v
```

The five Blueprint signatures and source-code hashes were checked after the run.
Public report signatures establish the declared signing key, not an independently
trusted identity. Full token replay needs the workspace's public artifact pool
and profile ledgers. See [the profile architecture](../../model_profiles.md) for
scope, reproduction and the remaining consensus/scheduling integration.
