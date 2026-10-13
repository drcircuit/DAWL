# DAWL platform execution record — 2026-10-13

The actual ingestion → token snapshot → input-vector reconstruction → independent
CPU audit path completed for all eight locked books. The research questionnaire is
not part of this platform. No model training took place.

## Executed result

| Measurement | Observed |
| --- | ---: |
| Original text | 5,850,515 bytes |
| Books / chunks | 8 / 1,438 |
| Real GPT-2 tokens | 1,657,163 |
| GPU-reconstructed float32 input vectors | 5,090,804,736 bytes |
| Dense token-sequence vector bytes written to disk | 0 |
| Token payload | 6,628,652 bytes |
| Shared real GPT-2 embedding table | 154,389,504 bytes |
| Public artifact package, ledger export and bootstrap policy | 169,193,602 bytes |
| CPU container audit | Every source, token and vector chunk passed |
| Ledger | 36 signed records; converged replicas |
| Final automated suite | 73 tests passed |

The GPU runtime profile records **1,438 DirectML Gather executions**. Recovery and
hashing took 13.94 seconds after table/session initialization;
this includes snapshot integrity checks and output commitments. The CPU container's
per-book replay times sum to 33.01 seconds,
including its first network download of the shared table. These are different scopes,
so their ratio is not a GPU speedup measurement.

Input vectors are real pretrained lookup rows, not random fixtures, contextual
representations or generated answers. CPU re-tokenization checks all original source
bytes; independent NumPy lookup reproduces every GPU vector commitment exactly.

## Storage accounting

Token payload alone is 99.8698% smaller
than storing a 768-dimensional float32 vector per token. Including the shared table,
all published source/artifact bytes, ledger export and public policy changes the
observed reduction to **96.68%** for this corpus.
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
| 1 | 17.42 | 17.10–17.57 | 1.00× |
| 2 | 9.80 | 9.78–9.89 | 1.78× |
| 4 | 6.15 | 6.09–6.35 | 2.83× |

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

Capture code commit: `211b21a39b60fe540c61024888c2497c62bc8a09`. Source, vector, benchmark and
initial container checks ran during implementation and carry their observed code
fingerprints. Those fingerprints are not all archived Git revisions. The final public
outage replay used the committed implementation. This distinction is retained instead
of assigning old measurements to a later code revision.

Dataset entry: `d912bf12b89bbb811cd768293d8ad301c4d6fbe4b16de2af9910547612a8ded5`. Ledger set digest:
`9394e3fd7dff3dac6446c7774606498ae89d99fb2fe341cf11f378917bb60c3a`. Independent policy pin:
`b859f160c0b3c79b710f6674c8e8110f5276de85ac97a7e4319a8a9d3094c91f`.

This is an operational local federation. Open discovery, distributed leases and
scheduling, independent operators, incremental admission, quotas, conflict governance,
larger heterogeneous workloads and training/weight lineage remain to be built. The
imported embedding checkpoint has a verifiable byte identity and extraction recipe;
its historical pretraining-data provenance is outside DAWL. Data lineage does not
establish answer correctness or a causal datapoint-to-answer relationship.
