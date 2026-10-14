# The Hypertrix
## Hybrid architecture and experimental results

Technical handoff for Yngvar | Experimental work by Espen | 14 October 2026

The Hypertrix is the working name for the DAWL platform: an open, distributed
system intended to connect contributed data, reproducible transformations and,
eventually, model weights through verifiable lineage. This report covers the
executed hybrid testnet and its local GPU extension. It is an engineering pilot
report, not a completed whitepaper or a claim of research novelty.

The prototype now joins a local WSL/Podman host to Azure Container Instances in
Norway East. Separate node containers ingest and serve data, replay transformations,
and commit accepted claims through a four-validator CometBFT network. A newcomer
added five books without replacing the original chain; an independent GPU node
then reconstructed the entire released corpus.

| Current result | Observed value |
| --- | --- |
| Released corpus | 13 books; 1,807 Frags; 2,061,330 tokens |
| Accepted consensus audit coverage | 34 jobs; 68 receipts; 3,614 Frag replays |
| Independent GPU diagnostic | All 1,807 Frags matched CPU bytes and ledger hashes |
| Compact transfer and local reconstruction | 172.5 MB fetched; 6.33 GB reconstructed and verified in 66.6 s |
| Cloud allocation | 3 vCPU / 8 GB, nine containers |
| Network membership | 29 node containers; two host failure domains; one operator |
| Model training and human-participant studies | Neither performed |

**Main finding.** The bounded ingestion-to-release mechanism works across the two
sites: content-addressed data, signed provenance, deterministic vector lookup,
asynchronous audits and certified release state agree. The evidence supports
continued systems research. It does not establish permissionless security,
production scalability, beneficial trust effects or the training history of
the imported embedding model. ([Pooled hybrid evidence](#evidence-guide); [Per-node hybrid evidence](#evidence-guide); [Newcomer contribution evidence](#evidence-guide); [GPU reconstruction evidence](#evidence-guide))

Evidence covers 13-14 October 2026 in Oslo (UTC+2). The GPU report was signed at
01:38:01 Oslo on 14 October. Measurements remain snapshots from a continuing
pilot; a completed long-duration availability study is not claimed.

<!-- page -->
# 1. Architecture and deployment

{{topology}}

| Hypertrix term | Implemented responsibility |
| --- | --- |
| The Collective | Signed role discovery and peer artifact transfer over an invited mesh |
| The Oracle | Four fixed validators order and certify deterministic application state |
| Cortex Units | Store source/token artifacts and the frozen embedding matrix |
| Daemons | CPU replay under execution leases; separate GPU diagnostic replay |
| Cipher-Logs | Signed lineage records and audit claims with explicit scope |
| The Blueprint | Immutable dataset release, with parent release and input manifests |

There are 29 node containers plus one transport gateway and two Console
containers: 32 containers in this pilot topology. Same-role nodes reuse an image
but have separate keys and state. The GPU container has a 2-CPU/2-GiB limit;
local CPU resources are additional to the cloud pool. The GPU shares the physical
local host and does not add a failure domain. ([Per-node hybrid evidence](#evidence-guide); [Newcomer contribution evidence](#evidence-guide); [GPU reconstruction evidence](#evidence-guide))

Azure's nine-container group shares networking, scheduling and lifecycle. Two
SSH connections separate consensus/control traffic from bulk artifacts. A third,
narrow connection was added for the GPU discovery endpoint. Public ingress is
SSH 2222 and Console HTTP/HTTPS 80/443. Validator application adapters use private
Unix sockets. No validator key is mounted into a provider, gateway or Console.
This is process/state separation within a trusted-operator pilot, not a hardened
multi-tenant boundary. ([Per-node hybrid evidence](#evidence-guide); [Network recovery evidence](#evidence-guide); [Azure container-group reference](#evidence-guide))

The following two landscape sheets expand this topology into a complete role,
protocol and data-flow map. Solid paths show the implemented prototype; dashed
amber notes show candidate hardening and future training/weight promotion.
Protocol IDs P0-P9 connect the role diagram to the wire/interface inventory.
The security notes identify residual risks, not completed mitigations. ([Implementation and protocol notes](#evidence-guide))

<!-- page -->
# 2. Data flow and mathematical contract

{{lineage}}

For a source chunk x, pinned tokenizer T and frozen input-embedding matrix E:

    t = T(x);  E in float32[V, d];  X[i, j] = E[t[i], j]
    V = 50,257;  d = 768;  t is uint32 little-endian
    c_tokens = SHA256(LE32(t))
    c_vectors = SHA256(row_major_float32_le(X))

Each Frag is a bounded source/token chunk. The Synapses in this experiment are
input-table rows, without positions, contextual layers, pooling or training.
Gather performs no floating-point arithmetic. The GPU implementation copies
32-bit words, so signed zero and subnormal bit patterns can survive exactly.
Bit-exactness here cannot be generalized to distributed floating-point training.

For L tokens, dense vector bytes equal 4Ld. The current corpus therefore represents
4 x 2,061,330 x 768 = **6,332,405,760 bytes**. The shared matrix is 4Vd = 154,389,504
bytes, and token IDs occupy 4L = 8,245,320 bytes before source and metadata.
Reconstruction trades repeated dense-vector storage and transport for a shared
table, compact tokens and local computation. It does not eliminate the need to
download and retain that table. ([GPU reconstruction evidence](#evidence-guide))

Artifact descriptors bind both byte length and SHA-256. Signed lineage entries
name parent IDs, signer, author sequence and predecessor. The local lineage
checkpoint hashes a canonical sorted set of entry IDs; it is not described here
as a Merkle inclusion proof. Corpus locks bind the exact acquired edition bytes;
registries bind their deterministic partitioning. Replay reconstructs original
parts, checks token snapshots, retokenizes and compares vector commitments.

The source corpus uses Project Gutenberg texts: original IDs 11, 76, 84, 345,
1342, 1400, 1661 and 2701; added IDs 35, 36, 62, 201 and 5230. Provenance records
source URLs and hashes; a signature alone is not a rights or factual-accuracy
determination. The handoff contains metadata and evidence, not book bodies or
the embedding/model weights. ([Per-node hybrid evidence](#evidence-guide); [Newcomer contribution evidence](#evidence-guide))

<!-- page -->
# 3. Trust, consensus and discovery

| Mechanism | What a successful check establishes | What it does not establish |
| --- | --- | --- |
| SHA-256 and exact size | Retrieved bytes match a commitment | Truth or availability of the source |
| Ed25519 signature | The claimed key signed the bound bytes | A unique person or honest execution |
| Independent replay | The checked transformation reproduces its commitment | Quality of future model outputs |
| Quorum certificate | State bytes are certified under pinned validator policy | Correctness of every worker's computation |
| DHT role record | A key advertised an endpoint/role for a bounded lifetime | Authorization, capacity or Sybil resistance |

**Consensus.** With four equal-weight validators, q = floor(2n/3) + 1 = 3 votes
are required. Two three-vote quorums intersect in at least 2q - n = 2 validators.
Under the protocol assumptions, fewer than one-third Byzantine voting power and
honest signing behavior prevent conflicting finalization. Liveness needs enough
reachable voting power and eventual network synchrony. A 2/2 site split therefore
halts both halves; one unavailable validator leaves three able to progress.
These are protocol properties, not evidence of four independent operators.
Observed fault tests are described in Section 4. ([Per-node hybrid evidence](#evidence-guide); [CometBFT implementation reference](#evidence-guide))

Keyless clients use the Go/CometBFT verifier, a pinned genesis, the signed header
and application-state hash, a minimum accepted height and a freshness window.
Unverified RPC responses do not silently substitute for a missing certificate.
The current validator set is fixed; the coordinated runtime upgrade is
publisher-driven and does not demonstrate decentralized upgrade governance.

**Discovery.** Peer IDs and role lookup keys use 160-bit hashes; routing compares
D(a,b) = integer(a XOR b). The bounded Kademlia-style implementation uses bucket
size 8, up to 3 parallel requests, and signed advertisements with lifetimes up to
180 seconds. Provider keys and endpoints are checked against certified provider
announcements. Its HTTP routes remain restricted to the invited SSH mesh.
Record replication does not replicate book/token artifacts. ([Newcomer contribution evidence](#evidence-guide); [Implementation and protocol notes](#evidence-guide))

**Sampling versus this run.** For uniform sampling without replacement of k
chunks from N, with m invalid chunks, P(detect) = 1 - C(N-m,k)/C(N,k).
This requires a fixed precommitted bad set and the specified sampling scheme.
The current hybrid release uses full assigned-shard replay with two distinct
admitted auditor keys per job. It is not a ZKP or proof that a remote signer did
the work; rewards, Sybil resistance and malicious-auditor incentives remain open.

<!-- page -->
# 4. Hybrid progression and fault tests

The first online baseline pooled role processes within each site's deployment.
The next stage used a fresh, separate chain with one container per admitted node.
Only public corpus/lineage inputs were reused; the two runs' validator identities
were distinct. Later contribution recovery preserved the second chain. ([Pooled hybrid evidence](#evidence-guide); [Per-node hybrid evidence](#evidence-guide))

| Measurement | Pooled hybrid baseline | Per-node hybrid baseline |
| --- | --- | --- |
| Books / Frags / tokens | 8 / 1,438 / 1,657,163 | 8 / 1,438 / 1,657,163 |
| CPU auditor membership | 20 | 20 |
| Accepted receipts / replays | 52 / 2,876 | 52 / 2,876 |
| Worker release to evidence capture | 305.113 s | 252.343 s |
| Cloud CPU allocation | 3 vCPU | 3 vCPU |

These are single executed runs, not a controlled comparative benchmark. The
per-node interval includes transfers, replay, coordination, fault exercises and
observation cadence. The separate initial seed transfer was 717,987,840 bytes in
121.35 seconds and preceded that interval. All 20 admitted auditors contributed
to the per-node run: 16 accepted receipts came from Azure and 36 from local
workers. There were 59 attempts, including seven temporary unavailable outcomes
that recovered. No expired leases, disputes or rejected transactions were
reported for that captured run. ([Per-node hybrid evidence](#evidence-guide))

| Deliberate check | Observation |
| --- | --- |
| Pause one Oracle process | Remaining three advanced from height 114 to 118 |
| Restart one local Daemon | Same registration and private state retained |
| Partition Azure/local 2/2 | Both halted at 267 for a 15 s stable observation after settling |
| Reconnect the sites | Chain advanced to 268; all four matched block hash at 296 |
| Probe private application interfaces | TCP ABCI connections refused from another container at each site |

The pooled run also halted both halves at 265 and resumed at 266. These bounded
experiments support the intended quorum behavior and recovery path. They do not
test Byzantine equivocation, prolonged partitions, lost disks, malicious hosts,
or tolerance of an entire site outage while maintaining progress. ([Pooled hybrid evidence](#evidence-guide); [Per-node hybrid evidence](#evidence-guide))

<!-- page -->
# 5. A newcomer grows the live corpus

The next test modeled a new data contributor with its own container and key,
read-only public bootstrap, and two discovery seeds. It announced PROVIDER and
TOKENIZER roles, discovered the pinned shared assets, ingested five additional
books and submitted a signed contribution manifest. Existing auditors found its
endpoint, rebuilt the contribution catalog and replayed the new shards. ([Newcomer contribution evidence](#evidence-guide))

The added works were The Time Machine (35), The War of the Worlds (36), A Princess
of Mars (62), Flatland (201) and The Invisible Man (5230). Raw texts were prefetched
before the node joined: this was a join/ingestion/audit trial, not a cold download
benchmark. The isolated newcomer launched at 01:16:57 Oslo on 14 October.

| Observation | Result |
| --- | --- |
| Newly contributed Frags / tokens | 369 / 404,167 |
| New audit jobs | 8, each with two distinct admitted signers |
| New receipts / checked Frag executions | 16 passes / 738 replays |
| New receipt sites | 13 local; 3 Azure; 16 distinct keys total |
| New failed receipts or remaining leases | 0 / 0 at capture |
| Local audit wall time: min / median / max | 18.862 / 25.441 / 26.175 s |
| Azure audit wall time: min / median / max | 112.545 / 112.827 / 113.650 s |
| Discovery snapshot | All 27 original peers returned the signed live provider record |

Audit time includes download, catalog validation and replay. Azure object caches
were cold after recovery while local caches were retained. The site timing
difference is therefore not an isolated hardware comparison. Historical transport
retries are distinct from failures of these eight new jobs.

The incremental Blueprint committed at height 1786, explicitly naming its
eight-book parent. Both releases remain immutable. The current union has 13 books,
1,807 Frags, 2,061,330 tokens, 34 completed jobs and 68 accepted receipts. ([Newcomer contribution evidence](#evidence-guide))

A separate keyless reader streamed both released input manifests, checked
uniqueness and book-level split separation, and counted 2,059,523 next-token
targets. The boundary invariant holds: targets = tokens - chunks. Sequences do
not cross source-chunk or split boundaries. This validates an input handoff;
no training job was started. New data still has one serving contributor, so
automatic artifact replication and provider-loss resilience remain untested.

<!-- page -->
# 6. Independent local GPU reconstruction

A fresh diagnostic node ran in its own Podman container on the NVIDIA GeForce
RTX 5090 Laptop GPU through WSL. It fetched public inputs through discovered
providers, verified fresh Oracle state and the signed lineage, then replayed
every released book. Its AUDITOR advertisement is discoverable but grants no
lease or consensus-auditor authority. All 29 current peers returned the correct
signed, unexpired GPU record. ([GPU reconstruction evidence](#evidence-guide); [NVIDIA CUDA references](#evidence-guide))

| Measurement, one full-corpus run | Observed result |
| --- | --- |
| Corpus coverage | 13 books; 1,807 Frags; 2,061,330 tokens |
| GPU / NumPy / committed hash comparison | 1,807 matches; zero mismatches |
| Real CUDA launches over corpus | 1,807; no CPU fallback |
| Dense vector bytes reconstructed | 6,332,405,760; none persisted |
| End-to-end fetch and verification | 66.577 s |
| Summed CUDA event kernel interval | 211.634 ms |
| Summed CUDA round-trip interval | 6.664 s |
| Summed NumPy reference lookup | 3.323 s |
| Downloaded application object bytes | 172,517,031 |
| Explicit resident GPU buffers | 204,786,688 bytes, excluding driver/context overhead |

The fixed kernel performs index arithmetic and 32-bit loads/stores. A shared
table stays resident, with reusable bounded buffers for one chunk at a time.
Each GPU result is compared byte-for-byte with NumPy before the existing verifier
checks the vector hash. The current backend lives outside the pinned core source
tree, and its script/kernel hashes are recorded in the signed report. No live
consensus implementation pin changed.

**Distributed-system benefit.** The node reconstructed and verified 6.33 GB of
dense vectors after fetching 172.5 MB of application objects in 66.6 seconds.
That is **36.7 times fewer payload bytes (97.3% less)** than transferring the
uncompressed dense vectors alone, including the shared matrix in this fresh-cache
fetch. This is a useful bandwidth result for the decentralized design. It is a
representation-and-reconstruction benefit shared by CPU and GPU implementations;
the test also establishes that a real GPU can reproduce every committed byte.

**Timing scope.** Small-chunk CUDA round trips totaled 6.664 s versus 3.323 s for
NumPy. This does not establish GPU acceleration of the local lookup. Kernel
events exclude transfers; CUDA round trips exclude final serialization, while
NumPy includes it. The 66.6 s run used local providers and included both GPU and
CPU verification. WAN time savings, compressed-vector baselines and warm-cache
benefits remain to be measured; they do not negate the observed payload reduction.

Fixtures covered repeated and boundary indices, partial blocks, signed zero,
subnormals and the maximum chunk size. Invalid indices and an injected bit flip
were rejected. Four focused tests passed, including GPU replay of a signed
fixture and rejection of a validly signed but incorrect vector commitment.
The node now watches for new Blueprints until the existing pilot deadline.

<!-- page -->
# 7. Recovery and operational observations

The move to nine Azure containers exposed a provisioning/ingress failure.
Containers could report running while the group stayed Creating without an
API-reported public IP. Internal SSH worked; external SSH and HTTPS did not.
Management authentication was valid; ordinary restart did not resolve that symptom.
Group and container port declarations agreed, and no customer VNet, NSG,
route table or managed firewall was attached. ([Network recovery evidence](#evidence-guide))

Closed node archives were hashed and read-verified before sequential disposable
network probes. The probes held no node keys or ledger state. Local signers and
the real cloud group were stopped during diagnosis, preserving signer fencing.
No live validator was cloned to another group.

| Selected controlled probe | Outcome |
| --- | --- |
| One container, 1 CPU / 1 GB | Public test payload reachable |
| Eight containers with original allocations | Reachable after 36 s |
| Nine containers with original explicit limits | Running containers, stalled public provisioning |
| Nine containers, no explicit limits | Reachable after 98.9 s |
| Nine, compute limits 3 CPU, auxiliary limits 0.3 / 0.2 | Reachable after 37.3 s |
| Same limits, public 80/443 declarations restored | Port 2222 test payload reachable after 41 s |

The real deployment recovered with the smaller gateway/Console CPU limits while
retaining 3 vCPU/8 GB total requests. This implicates the tested resource-limit
configuration, but does not establish Azure's internal root cause or a general
container-count rule. The successful port-declaration probe had no Console
listener; real HTTPS was verified separately after deployment. ([Network recovery evidence](#evidence-guide); [Azure container-group reference](#evidence-guide))

All seven cloud node archives restored their original identities and chain
history. The chain resumed at 1595 with its old Blueprint; the contribution
protocol activated at 1616. The cloud Console obtained a valid HTTPS certificate.
Both main Consoles showed fresh certified state and 28 topology/ledger nodes
before the GPU addition. The GPU has its own local page and site telemetry;
the main Console's fixed node list has not yet been expanded to that diagnostic.

These recovery observations establish same-chain continuity across a controlled
deployment change. They do not constitute an unattended long-duration soak test.
Infrastructure lifecycle details remain in the operator runbooks; this report
focuses on architecture, correctness and experimental evidence.

<!-- page -->
# 8. What is established, and what comes next

The platform is useful as an experimental substrate because its claims can be
made explicit and replayed. It separates data supply, deterministic processing,
audit claims, consensus certification and observational telemetry. That is the
foundation needed before studying how visible provenance changes a person's
trust in a trained model's output.

| Question | Current evidence | Next controlled experiment |
| --- | --- | --- |
| Can data be added without rewriting history? | One five-book newcomer, parent-linked release | Multiple simultaneous contributors, duplicate and conflicting proposals |
| Can independent execution reproduce vectors? | Full CPU coverage plus GPU/CPU/hash equality | Repeat across devices, drivers and batch sizes under fixed conditions |
| Can the network scale? | 20 auditors, 29 current node identities, 13-book corpus | Factorial corpus/concurrency runs; throughput, p95 latency, queues and transferred bytes |
| Is new data available through churn? | One new serving contributor; discovered by peers | Verified artifact replicas, then remove original provider and measure recovery |
| Is consensus resilient? | One-process pause and bounded 2/2 partition | Independent hosts/operators; longer partitions, disk loss, equivocation handling |
| Does provenance improve calibrated trust? | No human study or trained model yet | Controlled provenance visibility and correctness conditions with comprehension checks |

For the next performance study, distinguish cold and warm caches, randomize run
order, repeat each condition and keep compute allocations fixed. Measure acquisition,
tokenization, artifact transfer, replay, certification and publication separately.
Record failures and retries alongside successful throughput. Choose acceptance
thresholds before observing comparative results. The present timings are useful
engineering observations, not confidence intervals or scaling laws.

The compact transfer result supports reconstructing dense vectors near their
consumer. The GPU result also motivates batching or retaining vectors on device
when later compute uses them; local lookup acceleration is a separate question.
Training nodes and a complete trained-weight lineage remain subsequent stages.
The imported model/weight anchors and input inspection mechanisms are groundwork,
not a DAWL-trained model or proof of upstream training data. ([GPU reconstruction evidence](#evidence-guide); [Implementation and protocol notes](#evidence-guide))

Admission certificates, contribution reputation, rewards, slashing and dynamic
validator governance remain deferred. Signed claims alone do not solve collusion,
Sybil identities, fabricated approvals or eclipse attacks. Role separation and
container count must not be presented as independent economic actors.

<!-- page -->
# 9. Evidence, pins and reproducibility

<a id="evidence-guide"></a>

The companion ZIP contains this report, editable documentation, focused GPU
implementation/tests, standalone architecture sheets and selected public evidence. Its SHA-256 manifest and
verification script check package integrity and the GPU report signature. They
do not replace a full CometBFT certificate verifier. The pilot's Go verifier
checked the captured state certificates against the pinned genesis at execution
time. Historic discovery records are checked at their recorded observation time,
not treated as live advertisements. ([Per-node hybrid evidence](#evidence-guide); [Newcomer contribution evidence](#evidence-guide); [GPU reconstruction evidence](#evidence-guide))

| Evidence set | Archive path and purpose |
| --- | --- |
| Pooled hybrid evidence | evidence/pooled/ - first hybrid completion and partition snapshots |
| Per-node hybrid evidence | evidence/per-node/ - completion, audit analysis, faults, convergence and certificate |
| Newcomer contribution evidence | evidence/contribution/ - upgrade, newcomer, release, signed discovery and input check |
| GPU reconstruction evidence | evidence/gpu/ - signed report, runtime, input state, later certificate and discovery |
| Network recovery evidence | evidence/recovery/ - observed ingress controls and final deployment checks |
| Implementation and protocol notes | implementation/ and docs/ - focused reproducible code and protocol documentation |

The full DAWL repository is required for live replay. Reacquire the exact
hash-pinned public source/tokenizer/embedding assets and public bootstrap; use
fresh node identities. Never copy a live validator's keys or signing database.
The GPU runbook documents the WSL-specific build, launch, route and export steps.
Private keys, storage credentials, signed-URL secrets, private state archives and
raw corpus/model binaries are excluded from this handoff.

Chain: `hypertrix-dawlh1013185918`

    Genesis: 8443e106c542c1e920e51862b9763300bc2079871f9d03f1cc3c46ded5de1871
    Network: c2a69886ef30d988418d8dd9d130cdec15b8b5141c3ad9b14e02bd1dd6b1c888
    Blueprint: fd8760e4f3878a1bcb79645f42f0f55bcbbaab74f20d9aa2cfe53ac005743950
    GPU report: 26145a140ce3d60a3f9b26aec997b3b4c3b7f7f256253657b38e4c7f2339efd6

## Primary technical references

Microsoft: Container groups in Azure Container Instances. Shared group
resources/networking and public port declarations; accessed 14 October 2026.
https://learn.microsoft.com/en-us/azure/container-instances/container-instances-container-groups

CometBFT specification and verifier as used by the repository's pinned Go
module: consensus/go.mod and consensus/main.go. The four-validator experiment
does not claim deployment of independently administered validators.

NVIDIA: CUDA Driver API: execution control and event management; CUDA on WSL.
Used for the fixed Gather kernel and timing, not as evidence for measured results.
https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__EXEC.html
https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__EVENT.html
https://docs.nvidia.com/cuda/wsl-user-guide/contents.html
