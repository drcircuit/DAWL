# Hypertrix capability review — 15 October 2026

The limitations quoted from Oracle training run 003 describe that recorded run
correctly. Later work adds full-model training, exact optimizer replay and
governed recovery, each with a different verification scope. Historical reports
and signed archives are preserved; this review records the current position.

| Capability or quoted limitation | Current evidence and status | Work addressed or next validation |
|---|---|---|
| Local coordination, lineage, replay and replica availability only | Oracle run 003 demonstrates these end to end. Earlier hybrid trials also exercised cloud/host connectivity; neither is a WAN scaling study. | Keep scope attached to each run; measure throughput, latency and recovery across independent machines next. |
| No independent administrative trust | Still applies. Different keys/processes on one operator's machines establish role separation. | Independent operators, key custody and deployment authority remain necessary. |
| No permissionless admission | Still applies to trainers, verifiers and Oracle validators. Signed DHT role discovery and provider contribution do not confer training authority. | Keep shared-manifest mode; design admission, revocation and Sybil-resistant governance separately. |
| No poisoning resistance | Replay, finite-value checks and bounded update norms detect specific invalid computations. A correctly executed update on malicious data can pass them. | Add held-out quality and targeted poisoning experiments before claiming resistance. |
| No exact replay across GPU families | Still applies. Exact cohorts and independent branches are implemented in protocol 5; approximate cross-cohort acceptance is not. | Calibrate on actual hardware pairs, fixed recipes and adversarial cases. Do not tune thresholds solely to accept a failed run. |
| Asynchronous within a round, common verified parent between rounds | Still the Oracle training algorithm. It is leased local SGD with canonical token-weighted reduction. | Preserve as selectable strict mode. Stale-update acceptance requires a separate versioned optimizer contract. |
| Full-model backpropagation and optimizer state | Seed II completed full-model AdamW training and exact interval replay. Distributed full-model optimizer exchange is still absent. | Keep local full-model replay and network LoRA coordination as separately evidenced building blocks. |
| No optimizer sharding, model splitting or stale-gradient merging | Still applies. Exact optimizer-state restart/replay is implemented locally, not sharded across nodes. | Prototype outer optimizer/state exchange from a pinned common parent before adding staleness or model partitions. |
| No BitTorrent piece scheduling | True for the training artifact transport. Elsewhere, the swarm already has bounded 1 MiB piece pulls, piece caching, peer fallback and whole-object verification. | Integrate the transport into training, then test rarest-first scheduling, resume under churn and piece inventories. Existing piece transfer is not a complete BitTorrent scheduler. |
| Every contribution recomputed twice | True for run 003 and still the strict Oracle promotion rule. Seed II verified two 250-update intervals in separate processes. | Those fixed intervals are reproduction evidence, not an unpredictable anti-cheating sample. Post-commitment sampling and measured detection/cost remain open. |
| A corrupt/withheld submitted update stalls its stage | Live protocol-6 trial 003 recovered from an actual withheld GPU update. The replacement reproduced the same weight hash, then both verifiers approved. | Signed failure observations, owner rejection, revision fencing and reassignment are activated. Corrupt-byte and replay-mismatch handling also have CPU/HTTP tests; live malicious-update and multi-operator trials remain. |
| Missing required verifier or disputed votes stall forever | Live trial 003 cancelled a stage after its fixed deadline with one verifier absent; the verified head was preserved and a late vote rejected. | A replacement stage with an explicit roster completed under full quorum. Disputed-stage cancellation has transition tests; automatic replacement selection remains future work. |
| Durable artifact availability | Replication at verification time and cold-consumer recovery are demonstrated. Continuing possession is not. | Add epoch-bound possession challenges, retention leases, repair and independent storage operators. Recovery does not replace availability proofs. |
| Model quality gates | Seed II uses validation-based checkpoint selection and fixed held-out comparisons. Oracle promotion is based on computation replay, not quality improvement. | Version quality criteria and evaluation custody separately from replay acceptance. |

## New completed model evidence

[Seed II](results/scratch-003/README.md) trained 40,645,120 parameters from random
initialization on 8,577 documents, including 378 admitted Norwegian texts. Four
epochs completed 7,708 optimizer updates and 244,791,732 nonpadding targets.
Validation selected step 7,500. Fresh processes replayed intervals 0–250 and
7,250–7,500: 86/86 model-state tensors were bit-exact in each interval, and AdamW
state matched exactly. Full artifact verification hashed all 32 checkpoint
tensor files; the public release contains 36 signed lineage records and a seal
over 80 supporting files. Tensor files themselves remain in experiment storage.

The paired sampled test NLL is lower than both Seed I configurations in all four
domains. Architecture, corpus and training exposure all changed, so this result
does not isolate the effect of model depth. It is a completed local model
experiment, separate from the Oracle's distributed LoRA experiment.

## Recovery delivered in this change

The [recovery contract and operator commands](training_recovery.md) explain the
new transitions. The existing authority model remains explicit: a named verifier
reports an observation; the collaboration owner can discard work or cancel a
stage; only the unchanged full verification quorum can promote a checkpoint.
Reports and discarded votes stay auditable. A timeout is not proof of malice.

The extension is activated on the fresh [recovery testnet](results/training-recovery-003/README.md).
The authorized live trial completed seven real GPU contributions, ten bit-exact
replay checks and two fully certified checkpoints. Complete offline replay of
160 signed transactions over 1,952 blocks reproduced the final state exactly.
The retained final adapter was fetched through HTTP after the GPU workers
stopped. Historical protocol-4 state and its evidence remain unchanged.

Validation: 33 recovery, Oracle, collaboration and release checks passed, plus
40 cluster, profile, training/reduction and swarm checks. Eleven of these checks
exercise the new recovery and worker-fault paths. The archive-compatibility test
requires byte-identical state after replaying the original signed history.

## Next experiments, in order

1. Add durable availability challenges with delayed deletion, corrupt responses,
   provider churn and a bounded repair budget.
2. Compare full replay against committed interval sampling: verification time,
   transfer bytes, missed faults and false rejections, with unpredictable
   post-commitment challenges and complete restart state.
3. Extend training contracts to full-model optimizer exchange and an explicit
   outer optimizer; then evaluate staleness and heterogeneous cohorts separately.
4. Repeat across independently operated machines with controlled network faults
   and sustained workloads. Measure WAN behavior rather than extrapolating it
   from loopback throughput.

The [foundation and service-revenue proposal](collective_funding.md) is an
additional design track. Payment collection, reward accounting and governance
are not live services or evidence of permissionless economic security.
