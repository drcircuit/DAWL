# DAWL — Decentralized Asynchronous Weight Ledger

A research project toward a **Linux of AI**: independently operated infrastructure
for contributing data and compute, publishing model improvements, preserving their
provenance, and choosing or developing model branches.

We develop the theory and a working proof of concept together, one stage at a time.
The current stage runs real ingestion, a replicated provenance ledger, GPU input-vector
recovery and independent CPU replay. It performs **no model training or gradient updates**.

## Current platform prototype

The [platform runbook](docs/platform.md) describes the executable hats and commands.
The [recorded platform validation](docs/platform_run_001.md) separates measured results
from remaining engineering work. The local observatory is at `http://127.0.0.1:8880/`.

- Eight locked public-domain books feed independent CPU tokenization jobs.
- Signed lineage records connect original bytes, source registries, pinned tokenizer,
  token shards, a real GPT-2 embedding table, vector commitments and audit receipts.
- The GPU reconstructs 768-dimensional input embeddings from token IDs. Dense vectors
  are hashed and discarded; another node can reconstruct them from the pinned table.
- A separate CPU container downloads public artifacts and repeats all source, token
  and vector checks. Ledger peers replicate signed records into independent databases.
- Anyone with independently pinned public trust inputs can run `dawl_platform.py verify`;
  no producer key or admitted signing identity is needed for verification.

This is a bounded local federation, with static job assignment and explicit peer sync.
It does not yet provide open peer discovery, adversarial consensus, a distributed job
queue, training-run lineage or provenance for a generated answer. Imported GPT-2
pretraining is an explicit boundary. The research branch and its provisional study
materials remain separate; model trust experiments follow the platform work.

## Preserved ingestion-node baseline

| Hat | Responsibility |
| --- | --- |
| Source provider | Register exact source bytes and declared provenance before mining |
| Tokenizer/miner | Apply a pinned real ByteLevel BPE tokenizer and sign immutable token shards |
| Storage | Retain artifacts and serve bounded HTTP reads and challenge evidence |
| Validator | Independently sample committed chunks, re-tokenize, and sign audit receipts |
| Consumer | Download and verify complete token shards into a separate local store |

Roles have separate commands, HTTP routes, trust inputs, and signing identities.
The earlier pilot uses local CPU ingestion plus separate CPU storage and validator
containers. The platform above adds a defined GPU vector-recovery workload.
A physical node may eventually wear multiple hats without merging their contracts.

Start with the [architecture and deployment guide](docs/ingestion_nodes.md).
[Execution results](docs/ingestion_nodes_results.md) record what has actually run.

## Run the earlier ingestion-only pilot

Use Python 3.10+ and install the ingestion dependencies in a project environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ingestion.txt
.\.venv\Scripts\python.exe scripts/fetch_ingestion_assets.py
.\.venv\Scripts\python.exe scripts/prepare_ingestion_demo.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The fixture downloads an existing pinned GPT-2 tokenizer and Project Gutenberg's
Alice text, retaining the source notice. No model weights are downloaded. Inputs,
keys, snapshots, and receipts live under the ignored `artifacts/` directory.

Choose a container runtime:

```powershell
# Docker Compose
docker compose --env-file artifacts/ingestion-demo/deploy.env up --build -d

# WSL Podman (the runtime used for this workspace's pilot)
wsl -d kali-linux -u root --exec sh scripts/ingestion_containers.sh build
wsl -d kali-linux -u root --exec sh scripts/ingestion_containers.sh up
```

Once services are running, verify audits and copy the token shards from Windows:

```powershell
.\.venv\Scripts\python.exe scripts/check_ingestion_deployment.py
```

For individual hats, run `python dawl_node.py --help`. The architecture guide
specifies the JSONL input contract, pinned trust inputs, and HTTP interfaces.

## What the evidence establishes

Ed25519 signatures authenticate a pinned contributor key. Full-file verification
checks integrity and coverage. Independent re-tokenization checks source/token
consistency for the sampled chunks. These are different claims: correctly signed,
uncorrupted files can still contain incorrectly tokenized data.

Sampling is unpredictable and verification is deterministic. With N committed
chunks, m bad chunks, and k distinct uniform samples, detection probability is
`1 - C(N-m,k)/C(N,k)`. A passed partial audit does not validate unsampled chunks.
This is a commit-and-challenge audit, not zero-knowledge or proof of useful work.
Data quality, rights, availability over time, public identity admission, incentives,
and model training remain separate research and engineering stages.

## Original Stage 1 experiment

The dependency-free protocol experiment remains available:

```console
python -m unittest discover -s tests -p test_stage_1.py -v
python dawl.py
python dawl.py --input my-corpus.txt --samples 64 --output artifacts/my-corpus
```

It uses Unicode scalar token IDs and a synthetic embedding table. Its 99% saving
compares token IDs against dense float32 embeddings, not against raw text.
It is not the tokenizer or storage architecture of the current ingestion pilot.
See the [Stage 1 paper and research roadmap](docs/stage_1_data_ingestion.md),
[first results](docs/stage_1_results.md), and [metrics](docs/stage_1_results.json).

## Implementation map

| Location | Responsibility |
| --- | --- |
| `src/dawl_node/source.py` | Source admission, exact deduplication, bounded UTF-8 chunks |
| `src/dawl_node/tokenizer.py` | Pinned real tokenizer and encoding contract |
| `src/dawl_node/store.py` | Signed publication, disk storage, complete-file verification |
| `src/dawl_node/audit.py` | Independent challenges and deterministic verification |
| `src/dawl_node/service.py` | Separate storage and validator HTTP services |
| `src/dawl_node/transport.py` | Bounded peer transport and consumer replication |
| `src/dawl_node/common.py` | Canonical data, immutable files, signing identities |
| `dawl_node.py` | Role commands |
| `scripts/` | Fixture preparation, container lifecycle, live deployment checks |
| `tests/test_ingestion_*.py` | Persistence, adversarial inputs, real HTTP role boundaries |
| `src/data_ingestion/` | Original Stage 1 implementation and shared Merkle primitives |
