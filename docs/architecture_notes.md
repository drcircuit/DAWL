# Hypertrix architecture and hardening notes

Snapshot: 14 October 2026. Companion to the two landscape architecture sheets in
the research report. This document changes no live permissions, admission rules,
deployment settings or deadlines. All H1-H8 mechanisms below are proposals for
subsequent design and testing, not claims of implemented security.

## Reading the diagrams

Teal paths carry artifacts or local publication. Blue paths carry signed commands
or certified state; a two-ended arrow denotes request/response traffic. Grey
paths carry observations. Amber dashed boxes identify proposed hardening or the
future training path. P0-P9 are interface identifiers, defined on the second
sheet. H1-H8 connect concrete risks to possible controls.

Logical roles are separate from deployment counts. The newcomer combines the
contributor/tokenizer and one Cortex role in one container. The pilot has four
Oracle validators, three Cortex providers, twenty CPU Daemons, one publisher and
one GPU diagnostic: 29 node containers. One gateway and two Consoles bring the
total to 32 containers. Nine are in Azure; 23 are on the local host. This gives
two host failure domains under one operator, not 29 independent operators.

Validators advertise VALIDATOR and LEDGER_REPLICA. The publisher also advertises
LEDGER_REPLICA, but has no extra consensus vote. There is no separate dedicated
ledger-replicator deployment. Consensus replication copies application state;
signed ingestion-lineage records and source/token artifacts are served by data
peers. Discovery-record replication does not replicate the underlying corpus.

## End-to-end data and authority flow

1. **Bootstrap (P0):** a joiner reads the public network/genesis/plan and runtime
   pins, creates its own key and owns its private state. Current admission and
   protocol activation remain controlled by the pilot's existing trust policy.
2. **Ingest (P1/P2):** acquire edition bytes, pin their source lock, deterministically
   partition and tokenize, gather frozen embedding-table rows, and commit hashes.
   Sign parent-linked source/token/vector records and a contribution manifest.
   The contributor serves compact artifacts through its Cortex endpoint.
3. **Discover and announce (P6/P4):** signed, expiring DHT records advertise roles
   and endpoints. A signed provider announcement and contribution transaction
   enter Oracle state. Discovery is distinct from authorization.
4. **Audit (P3/P4/P5):** admitted CPU Daemons claim height-bounded leases, fetch
   missing artifacts, verify lineage and source/token bindings, retokenize, replay
   the Gather operation, and submit signed outcomes. Oracle verifies command
   authorization and deterministic policy; it does not execute the data replay.
   Each completed job requires claims from two distinct admitted auditor keys.
5. **Release (P4/P5/P8):** the publisher observes certified completion and publishes
   a Blueprint linked to its parent release. Four fixed CometBFT validators order
   deterministic state transitions; finalization requires three votes. Readers
   verify signed headers and the state hash against the pinned genesis, accepted
   height and freshness policy before trusting release metadata.
6. **Independent reconstruction (P3/P5):** the GPU diagnostic fetches the released
   inputs, verifies their lineage and reconstructs every Frag. CUDA bytes must
   equal NumPy bytes and the committed hash. Its signed report is independent
   diagnostic evidence, not a consensus audit receipt. Advertising AUDITOR gives
   it no lease authority.
7. **Observe (P7/P9):** Consoles combine certified state with observational node
   feeds. They distinguish verified ledger state from unsigned telemetry. The
   GPU also has a separate local status page. The main Console's fixed topology
   list does not yet include the GPU diagnostic. Cross-site private HTTP and
   CometBFT traffic use pinned, authenticated SSH routes in this pilot.

## Protocol details and boundaries

| Interface | Implemented behavior |
| --- | --- |
| P0: bootstrap | Public SHA-256 pins and Ed25519 registrations; separate node keys/state. Coordinated publisher-signed upgrade activation. |
| P1: acquisition | HTTPS source fetch with URL and exact edition-byte lock. |
| P2: ingestion | Pinned tokenizer; uint32 little-endian token IDs; frozen float32 table; SHA-256 length-bound descriptors and signed parent DAG. |
| P3: artifact pull | Private HTTP 8900. CPU transfer uses 1-MiB pieces, bounded concurrency, deduplication, retry/failover and final object hashing. GPU/public readers use bounded whole-object fetches. |
| P4: commands | Private RPC 26657; canonical JSON, Ed25519, chain/plan binding, author sequence and validity height. Provider announcements, contribution manifests, leases, outcomes and releases. |
| P5: certified reads | Go/CometBFT verification of the fixed-genesis quorum header and app-state hash, plus height/freshness checks. |
| P6: discovery | HTTP 8903; 160-bit XOR routing, bucket size 8, parallelism 3, signed records with at most 180-second lifetime and refresh every 25 seconds. Restricted invited routes. |
| P7: observations | Cloud feed 8901, forwarded local feed 8902, local Console 8882, GPU page 8883; public Console via Caddy HTTPS 443. |
| P8: consensus | Validator P2P TCP 26656; three-of-four quorum; private Unix-socket ABCI per validator. Persistent signer state; incident evidence without automatic punishment. |
| P9: transport | Local outbound SSH to Azure gateway 2222; pinned host key and authenticated operator, restricted forwards, separate control/bulk connections and a narrow GPU discovery connection. |

Local containers use host networking; Azure containers share group networking and
lifecycle. Private node ports are not separately exposed at public ingress. The
gateway and Console have no validator keys. These are useful separation measures
inside this operator-controlled deployment, not a tested hostile-tenant boundary.

## Why reconstruction matters to a distributed system

For token count L, vocabulary V and vector dimension d, dense sequence-vector size
is `B_dense = 4Ld`. The cold representation contains approximately
`B_compact = 4Vd + 4L + B_source_and_metadata`. The shared table cost is amortized
across all Frags using that exact table. A warm cache can reuse it; updates to the
table change its commitment and may require another download.

The executed GPU diagnostic fetched **172,517,031 application object bytes**,
reconstructed **6,332,405,760 vector bytes**, and verified the full corpus in
**66.5765 seconds**. The ratio is **36.706**, or **97.276% less payload** than the
uncompressed dense vectors alone. The table is included in the fetched total.
This demonstrates the representation's bandwidth benefit and a working GPU
reconstruction path. The same compact representation can be reconstructed on CPU.

The measured local CUDA round trips did not beat the NumPy reference timing;
that is a separate result from reduced data movement. The full run included both
implementations and verification, and fetched from local providers. It therefore
does not measure WAN completion time or a speedup over transferring compressed
dense vectors. No claim about a fast local drive is needed to motivate this design.

The next comparison should reconstruct from compact artifacts versus download
the same dense bytes, under matched link rate, latency, loss, cache state and
verification work. Include compressed dense artifacts, cold and warm shared-table
caches, CPU and GPU reconstruction, and vectors consumed on device. Record actual
wire bytes, completion time, peak memory, CPU/GPU use, retries and hash equality.
Repeat and randomize conditions. This separates transport savings, local compute
cost and end-to-end benefit without treating one as a substitute for another.

## Candidate hardening and validation

| Diagram note / risk | Candidate mechanisms | Required evidence and remaining limits |
| --- | --- | --- |
| H1: unauthorized roles and key churn | Scoped, expiring role credentials; explicit revocation; CA-issued or network-approved admission as alternative models. | Reject wrong-role, expired and revoked credentials under partitions. Define rotation/recovery. Credentials alone neither establish unique operators nor remove authority concentration. |
| H2: eclipse, Sybil identities and resource exhaustion | Diverse independently operated seeds; peer-selection diversity; admission/storage/request quotas and bounded work. | Surround a target with corrupt, stale and withholding peers; measure discovery recovery and resource bounds. Signed advertisements alone cannot establish routing diversity. |
| H3: unavailable data under churn | Verified replicas on independent hosts, repair assignments and unpredictable retrieval checks. | Remove the original contributor and recover every committed artifact within a declared window. Measure repair under loss and stale announcements. Hash correctness does not ensure availability. |
| H4: collusion and dummy approvals | Unpredictable post-commit challenges, independently assigned replay, canary cases and dispute evidence. | Introduce deliberately false approvals and coordinated auditors; measure detection, false accusations and cost. Challenge generation must resist prediction/manipulation. A replay receipt is not a ZKP or proof of remote execution. |
| H5: signing compromise and centralized upgrades | Independent validator operators; protected remote signing with anti-double-sign state and fencing; threshold-reviewed release metadata; recovery drills. | Exercise signer failover without duplicate signatures, conflicting votes and unauthorized/expired/rolled-back releases. A protected key alone does not prevent double-signing. Punishment requires explicit adjudication and governance. |
| H6: hostile transport, co-tenants and forged telemetry | Peer-bound TLS/Noise or managed mTLS; network/workload ACLs, isolation and quotas; signed telemetry when relied upon. | Reject wrong peer identities, stale/replayed observations and forbidden lateral access. Distinguish an authenticated channel from permission to audit or vote. Define NAT/relay behavior before public deployment. |
| H7: poisoning, duplicates and unsuitable source material | Source/rights policy, contributor provenance, deduplication, quarantine and corpus-quality review. | Test conflicting editions, duplicates, poisoning and removal/versioning requests. Provenance binds origin and bytes; it does not certify truth, quality or suitability. |
| H8: future training and weight claims | Bind dataset release, parent weights, recipe, seed and environment; checkpoint manifests and independently specified validation/evaluations before promotion. | Start with bounded training reproducibility tests and measured cross-device tolerances. Gather copies exact table bytes without arithmetic; that result does not establish floating-point training reproducibility. |

Lease timeout is an availability observation, not proof of malicious intent.
Any future point/reward or slashing system needs a separate policy for objective
contradictions, benign failures, disputes and appeals. These policies, dynamic
validator governance and role certificates remain deferred as agreed.

## Source map and external design references

The implementation evidence is the repository's `client.py`, `state.py`,
`incidents.py`, `worker.py`, `dht.py`, `transfer.py`, `contributor_node.py`,
`gpu_reconstruction_node.py`, `hybrid_observer.py` and `consensus/main.go`.
Measured outcomes are in the handoff's named `evidence/per-node/`,
`evidence/contribution/`, `evidence/gpu/` and `evidence/recovery/` directories.
The diagram is editable through `scripts/build_architecture_sheets.py`.

The following references inform candidate controls; none implies these systems
are integrated into the current pilot:

- [libp2p secure channels](https://libp2p.io/docs/secure-channels-overview/): describes TLS 1.3 and Noise as secure-channel options. A possible basis for H6's future peer transport.
- [The Update Framework: roles and metadata](https://theupdateframework.io/docs/metadata/): defines signing roles, signature thresholds, delegated trust, expiration and consistency metadata. A possible basis for H5's release trust and key separation; not a consensus governance solution by itself.
- [CometBFT signing specification](https://github.com/cometbft/cometbft/blob/main/spec/consensus/signing.md): relevant to signer state and conflicting votes. Deployed behavior must be checked against the version pinned in `consensus/go.mod`.

References reviewed 14 October 2026. The proposed validation experiments are
Hypertrix research plans, not results reported by these external projects.
