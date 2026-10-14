# Local Oracle training testnet 003

Executed 14 October 2026. The testnet completed real SmolLM2-135M adapter training
under consensus-managed leases, replaced a departed trainer, and committed four
weight checkpoints after independent GPU replay. The network remains online for
inspection: [Console](http://127.0.0.1:8882/) and
[GPU verification control plane](http://127.0.0.1:8882/gpu).

See [architecture, protocols, mathematics and hardening](../../oracle_training.md)
for the acceptance contract and complete data-flow diagram. This run supersedes
the fixed-roster coordination experiment without altering its historical evidence.

## Deployment and workload

| Component | Execution |
| --- | --- |
| Oracle | Four CometBFT validators, separate containers, keys and databases |
| Cortex and discovery | Source/model provider plus signed XOR-DHT discovery |
| Source auditors | Two CPU containers; completed all 13 books |
| Training | Two successful GPU trainers; a third deliberately abandoned a lease |
| Verification | Two GPU verifier containers, each replaying every contribution |
| Observatory | Public observer container plus host-only resource collector |
| Hardware boundary | One Windows/WSL host, one RTX 5090 Laptop GPU, one operator |

The corpus contains **13 books, 1,807 Frags and 1,933,422 tokens**. A fresh v3
Blueprint received **26 source-audit receipts**. The v1/v2 upgrade bootstrap used
an explicitly synthetic one-token fixture; these bootstrap approvals are not
counted as corpus audits or training evidence.

The v4 training stage used four shared-parent rounds, two shards per round, two
local SGD steps per shard and batch size two. It covered **32 distinct training
windows / 3,950 valid next-token targets**. This is a bounded partial pass over
the verified corpus, not a full epoch or evidence of improved model quality.

SmolLM2-135M's base and shared LoRA A projection stayed frozen. Trainers updated
184,320 FP32 B values. Each contribution was **737,280 bytes**, with canonical
target-token-weighted aggregation. Verifiers independently reconstructed parents,
repeated every training step and computed the aggregate before voting.

## Observed outcomes

| Check | Result |
| --- | --- |
| Accepted training contributions | 8 from two successful trainers |
| Numerical replay | 16/16 bit-exact checks, eight per verifier |
| Weight ledger | Four checkpoints, each with both required verifier receipts |
| Trainer churn | One expired lease; newly admitted trainer completed the same shard under a new generation |
| Late submission | Correctly signed expired-owner result rejected; state unchanged |
| Validator outage | Validator 3 stopped at height 893; remaining three certified height 1,133 |
| Offline ledger verification | Quorum signature check plus 127 application transactions replayed over 1,133 blocks; exact state match |
| Surviving replica | Fresh consumer recovered the executable composite model from verifier 1 alone |

The replacement trainer was admitted after stage registration. Training ownership
was not tied to a fixed pair of names; the final round also assigned its shard
order differently. Checkpoint promotion required matching verifier commitments,
not a designated trainer acting as the publisher.

For the availability test, the original provider, discovery bootstrap, both
successful trainers and verifier 0 were stopped. A consumer with no cached
objects and no signing keys fetched **299,667,966 bytes in 3,680 objects** from
verifier 1. It checked the pinned quorum certificate, replayed the application
history, retokenized all source books, verified the complete base-model hash,
loaded the final adapter and ran a CPU forward pass on two validation windows.
The observed loss was 3.32249 over 256 targets; this only checks executability
and is too small to support a quality comparison.

After evidence export, the provider, discovery, both trainers, verifier 0 and
validator 3 were restarted using their existing state and identities. Completed
source auditors and the intentionally departed trainer remain stopped and visible
as such. Trainers and verifiers now serve the completed checkpoint; they are not
continuously training new stages.

The successful trainers reported 20.886 and 20.239 seconds of training execution,
including first-call GPU warmup. These are per-node measured intervals, not total
pilot elapsed time or a distributed speedup benchmark. Transfer totals and peer
sources are recorded in [summary.json](summary.json).

## Evidence and independent checks

- [Summary and measurements](summary.json)
- [Quorum certificate and certified state](training-certificate.json)
- [Complete nonempty-block application history](application-history.json)
- [Offline verification result](verification.json)
- [Stage contract](stage.json) and [executable manifest](manifest.json)
- [Lease expiry and replacement evidence](churn.json)
- [Cold-consumer result](availability.json)
- [Source-corpus audit summary](source-report.json)
- Verifier replay records in [verifier-0](verifier-0/) and [verifier-1](verifier-1/)

Genesis SHA-256, to obtain through a trusted channel when checking this archive:

```text
5764519561f755af339ee6a0898dee7e2f8fe5966f489bccdc72b500959bb9a9
```

Final checkpoint:

```text
68853874466e8f2cd0ef56af38e7cd2557d356b009cb5afe6f2d8bf367b8207f
```

Pinned source implementation:
`c89b9198469d025d2eb6985098ce44a1e8d7b6257abe1e4c739bbed8c86563b7`.
Execution image `localhost/hypertrix-oracle-training:003`, ID
`cee94a1cc77cf199787333c8f39d7b04a5d08c6decfb6cacb227aa8c1a492ca1`.
The full base-model SHA-256 is
`80521b40281d6ce74e35c9282c22539e75aa0ac8578892b2a59955ef78d55da1`.

From a matching runtime with `check-certificate` available, run:

```sh
python -P scripts/verify_oracle_training.py \
  --root docs/results/oracle-training-run-003 \
  --genesis-sha256 5764519561f755af339ee6a0898dee7e2f8fe5966f489bccdc72b500959bb9a9
```

The archive contains public evidence, not node signing keys or the model bytes.
The offline verifier checks public signatures and all deterministic application
transitions. Repeating numerical GPU execution additionally requires the pinned
runtime and committed model/data objects; signed verifier logs alone cannot prove
that independent physical machines performed the work.

## Scope and remaining work

The run demonstrates local coordination, lineage, replay and replica availability.
It does not establish WAN scaling, independent administrative trust, permissionless
admission, poisoning resistance, or exact replay across different GPU families.
Training is asynchronous within a round and waits for a common verified parent
between rounds. It does not yet implement stale-gradient merging, model splitting,
optimizer sharding or BitTorrent piece scheduling.

Every contribution was recomputed twice for verification. This intentionally
expensive baseline needs measured sampling alternatives before a large network.
An accepted corrupt or withheld update, or an unavailable required verifier, can
still stall its stage. Rejection/replacement procedures, durable availability
challenges, multi-operator deployment and quality gates are the next hardening work.

Validation included the repository regression suite (202 passed, four GPU-specific
tests skipped in the CPU test container), two additional numerical work-catalog
tests, and the actual GPU/container experiment above. The live Console was checked
for node detail inspection, role filtering, GPU routing and browser errors.
