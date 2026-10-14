# The Hypertrix

## Public research observatory

[Live Azure observatory](https://dawlh1013185918state.z1.web.core.windows.net/) ·
[Norwegian technical handoff](docs/research-handoff-2026-10-14.md) ·
[Fixed-advice participant pilot](docs/trust-study-v2.md) ·
[Publication and network-split design](docs/public_observatory.md)

Local compute publishes quorum-checked ledger snapshots to a read-only Azure
site. The older cloud compute pilot is stopped with closed signing-state archives
preserved. Participant responses stay local. The [strict full-epoch run](docs/results/finetune-strict-002/README.md) completed
228 updates and replayed all 392 adapter tensors exactly. Independent CPU checks
confirmed all 4,427 token windows and 310 merged tensors. This is a new lineage;
the original failed replay stays recorded. The [roadmap](https://dawlh1013185918state.z1.web.core.windows.net/#roadmap)
tracks completed, active and planned platform work.


A research project toward a **Linux of AI**: independently operated infrastructure
for contributing data and compute, publishing model improvements, preserving their
provenance, and choosing or developing model branches.

We develop the theory and a working proof of concept together, one stage at a time.
The current stage runs real ingestion, a replicated provenance ledger, GPU input-vector
recovery, independent CPU replay and Oracle-managed training. The
[local training testnet](docs/results/oracle-training-run-003/README.md) continues
SmolLM2 with leased training shards, replacement trainers, two GPU replay verifiers
and four Oracle-certified weight checkpoints. A cold consumer recovered the model
and its provenance from one surviving verifier after the original provider and both
trainers stopped. The earlier [fixed-roster run](docs/results/training-run-001/README.md)
remains a preserved baseline.

The Hypertrix is the platform; **DAWL (Decentralized Asynchronous Weight Ledger)**
remains the name of the underlying research and implementation. See the
[terminology](docs/hypertrix_terminology.md) for The Collective, The Oracle,
Cortex Units, Daemons, Frags, Synapses, Cipher-Logs and The Blueprint.
The [hybrid testnet runbook](docs/hybrid_testnet.md) describes the current
one-container-per-node Azure/local deployment and its shared 3-vCPU cloud pool.
The [Hypertrix validation report](docs/hypertrix_run_001.md) records full-corpus
completion, individual-node outages and WAN quorum loss/recovery.
The [contributor protocol](docs/contributor_protocol.md) adds signed DHT role
discovery, incremental corpus proposals and append-only Blueprint releases.
Auditor/Oracle certificates, reputation and reward policies are deferred.
The [model-profile experiment](docs/model_profiles.md) adds separate tokenizer/model
ledgers and token Blueprints over a shared artifact store. [Protocol 3](docs/profile_protocol_v3.md)
implements certified profile registration, compatible worker routing, independent
Blueprint heads and offline `o200k_base` support. Its local tests precede activation
on the hybrid consensus chain.
The [six-profile cluster results](docs/results/profile-testnet-002/README.md)
record 78 completed jobs, 156 signed audit receipts, validator-outage recovery
and offline quorum/lineage verification.
The [multi-profile GPU verification](docs/results/profile-gpu-run-001/README.md)
binds real imported input weights to those certified token Blueprints, then checks
CUDA reconstruction against signed CPU commitments through a separate Cortex provider.
The [o200k GPU addendum](docs/results/o200k-gpu-run-001/README.md) extends this to
real GPT-OSS input weights after verifying ordinary-token compatibility, with an
explicit link from the original token Blueprint to a separate model-bound profile.
**The Hypertrix Console** runs at `http://127.0.0.1:8882/`.
Its [GPU verification control plane](http://127.0.0.1:8882/gpu) shows verifier
receipts, checkpoint acceptance, Execution Leases and shared-device telemetry.
The [Experiments & evidence presentation](http://127.0.0.1:8882/experiments)
explains 13 recorded experiments with measured graphs, equations, interactive
quorum/sampling illustrations and 38 inspectable public evidence files.
The [protocol-4 architecture and runbook](docs/oracle_training.md) describe the
running local testnet and its current trust and scaling limits.
The opt-in [collaboration modes and branch protocol](docs/collaboration_modes.md)
preserve homogeneous exact replay under a shared participation manifest and add
independent model histories across execution cohorts. An offline
[signed metadata rehearsal](docs/results/collaboration-rehearsal-001/README.md)
demonstrates explicit forks and consumer choice; heterogeneous tensor verification
and DiLoCo aggregation remain future work.
The [Branches & modes explorer](http://127.0.0.1:8882/branches) distinguishes this
rehearsal from the current live protocol-4 network.

The [local economics fine-tuning methodology](docs/finetuning_lab.md) adds a
Qwen3-0.6B continuation on 16 books and a [Trust Lab](http://127.0.0.1:8885/).
It compares actual Ollama and OpenAI responses, explains development provenance
in plain language, and preserves an identical-answer A/B/C disclosure experiment.
This calibration branch has its own signed records and verification scope.
The [completed pilot report](docs/results/finetune-economics-001/README.md)
and [shareable evidence bundle](docs/results/finetune-economics-001.zip) record
three completed epochs, successful artifact checks and the failed exact GPU replay.
The [repeatability investigation](docs/results/finetune-repeatability-001/README.md)
isolates the first backward-pass divergence and measures strict execution fixes.
The [verification design](docs/training_verification.md) separates exact replay,
future interval audits, numerical agreement and evidence for worker rewards.

## Current platform prototype

The [platform runbook](docs/platform.md) describes the executable hats and commands.
The [recorded platform validation](docs/platform_run_001.md) separates measured results
from remaining engineering work. The local observatory is at `http://127.0.0.1:8880/`.
The [swarm runbook](docs/swarm.md) and [swarm validation](docs/swarm_run_001.md) cover
the asynchronous network layer and its five-container adversarial experiment.
The [consensus cluster runbook](docs/cluster.md) adds four CometBFT validators,
leased complete-corpus replay, dataset release rules, streaming training inputs
and an imported-weight ledger foundation. Its live view is `http://127.0.0.1:8882/`.
The [cluster validation report](docs/cluster_run_002.md) records the full-corpus
run, quorum loss and recovery, public certificate verification and current limits.
The [external cluster workflow](docs/external_cluster.md) adds operator-local key
generation, a shared public genesis bundle, per-host deployment, certified worker
reads and attributed incident records. The hybrid pilot now connects Azure Norway
East with the local machine through an authenticated WAN transport.
The [external rehearsal results](docs/external_run_001.md) record full-corpus
completion despite network isolation, forged RPC state and corrupted artifact pieces.

- Eight locked public-domain books feed independent CPU tokenization jobs.
- Signed lineage records connect original bytes, source registries, pinned tokenizer,
  token shards, a real GPT-2 embedding table, vector commitments and audit receipts.
- The GPU reconstructs 768-dimensional input embeddings from token IDs. Dense vectors
  are hashed and discarded; another node can reconstruct them from the pinned table.
- A separate CPU container downloads public artifacts and repeats all source, token
  and vector checks. Ledger peers replicate signed records into independent databases.
- Anyone with independently pinned public trust inputs can run `dawl_platform.py verify`;
  no producer key or admitted signing identity is needed for verification.
- Admitted swarm peers discover one another, pull and resume hash-checked artifact pieces,
  gossip signed audit claims, independently recheck those claims and replicate observation batches.
- The live observatory shows topology, artifact availability, receipt outcomes and disagreements.

The original platform and swarm remain preserved baselines. The new admitted
cluster has consensus-ordered leases and complete sharded replay among configured
workers. Three of four validators are required to commit. It does not yet provide
permissionless admission or provenance for a generated answer. Protocol 4 adds
Oracle-certified training-run lineage on a fresh local chain. Imported GPT-2 and
SmolLM2 pretraining remain explicit boundaries. The research branch and its provisional study
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
Data quality, rights, availability over time, public identity admission and incentives
remain separate research and engineering stages. The new
[training stage protocol](docs/distributed_training.md) links executed local updates
to their parent model and certified token Blueprint, with explicit audit limits.

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

The [hybrid testnet runbook](docs/hybrid_testnet.md) covers a three-vCPU Azure
Container Instances pool in Norway East connected to a separate local pool,
with four validators and twenty auditors. It includes the EUR 50/four-hour
envelope, authenticated transport, certificate capture and cleanup. The earlier
72-vCPU AKS proposal is superseded for this pilot.

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
