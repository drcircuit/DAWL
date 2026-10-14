# Full-corpus consensus cluster validation

On 2026-10-13, DAWL completed its entire locked corpus through a four-validator
CometBFT 0.38.26 cluster. The validators agreed on leases, accepted auditor results,
finalized a dataset release and recorded a proposed training recipe. No model
training or weight update was executed.

This was a real process/network deployment on one physical Windows/WSL machine:
four validator containers, two artifact-provider containers, three CPU auditor
containers and one Windows CPU auditor. Containers had separate writable state
and a two-CPU, 2 GiB resource limit. The static bridge and loopback endpoints do
not demonstrate independence of machines, operators or administrative domains.

The [runbook](cluster.md) describes the protocol, limits and recovery commands.
The live observatory is [localhost:8882](http://127.0.0.1:8882/).

## Coverage and outcomes

| Measurement | Result |
|---|---:|
| Locked books | 8 |
| Source chunks | 1,438 |
| Source tokens | 1,657,163 |
| Planned shards | 26 |
| Required distinct auditor keys per shard | 2 |
| Accepted signed shard results | 52 |
| Chunk replay executions represented by those results | 2,876 |
| Lease attempts | 53 |
| Deliberately abandoned leases that expired | 1 |
| Disputed shards | 0 |
| Unit/integration tests | 95 passed |

Workers reconstructed source bytes, re-tokenized the assigned ranges and checked
NumPy input-vector bytes against the earlier host GPU commitments. This workload
replayed existing ingestion output; it did not acquire new books or schedule a
new GPU production run. The original 36-record provenance checkpoint was retained.

Worker results were distributed as follows: container workers 0, 1 and 2 accepted
1, 19 and 18 shard results respectively; host worker 3 accepted 14. Worker 0 was
deliberately withheld until its first lease expired, so these counts are not a
fairness or worker-performance comparison. Per-worker transfer counters are in
[capture.json](results/cluster-run-002/capture.json).

## Actual failure injection

The exercise began at 16:31:39 UTC. All offsets below are from the exercise start;
they include intentional outages, lease waiting, startup and cold artifact pulls.

| Probe | Observation | Elapsed seconds |
|---|---|---:|
| Stop one validator | Three validators advanced from height 123 to 127 | 9.079 |
| Stop a second validator | Height remained 128 for eight seconds; a valid claim entered the mempool but did not commit | 22.673 |
| Restore a third validator | The pending claim committed at height 130 | 35.260 |
| Leave its worker absent | Lease expired at height 190; stale result rejected at height 191 | 121.373 |
| Finish all corpus jobs | 26 complete shards and 52 passing results at height 210 | 146.863 |
| Publish dataset and training plan | Both requests finalized through consensus | 149.137 |
| Restart another validator | All four reported the same dataset and common block history | 155.508 |

These observations confirm quorum enforcement, recovery and lease fencing for
this run. They are not a throughput benchmark, a malicious-validator experiment,
a simulated network partition, or a proof of safety for every failure schedule.
Consensus safety still depends on CometBFT's assumptions and the deterministic
application being correct. A separate scaling study must vary worker count,
corpus size, latency and provider bandwidth under controlled conditions.

The rejected stale result was rejected by `CheckTx` before inclusion in a block.
Consequently the finalized-state rejected-transaction counter remains zero;
that counter does not count mempool rejections.

Two later operational checks passed: completed worker actor sequences stayed
unchanged while height advanced from 334 to 340, and provider 1 restarted without
a forced kill. Both providers remained healthy. The observatory returned HTTP
200 and its JavaScript passed syntax validation; no visual browser check was run.

## Public verification and training handoff

A fresh verifier container received only the public genesis file and its
independently pinned hash. CometBFT's verification library checked the fixed
genesis voting-power quorum and signed header. The application state at height
301 matched the application hash certified by the header at height 302:

`f13173d0847e476b45809d302384359889d8f94aa51d5f52af892dd59fbfd3dc`

The archived [certificate and state](results/cluster-run-002/public-certified-state.json)
are a snapshot. The chain continues advancing. A certificate authenticates the
agreed state; it does not prove worker computation, independent human identities,
source truth, or freshness. Worker signatures remain attributed claims. Anyone
can separately replay the pinned source/token/vector transformations.

A fresh training-reader directory containing only public `plan.json` fetched and
validated all token inputs and reconstructed the full parent checkpoint. No
private key was used. At sequence length 128 the reader produced:

| Split | Whole books | Sequences | Unmasked next-token targets |
|---|---|---:|---:|
| Train | 11, 76, 84, 1342, 1400, 2701 | 10,171 | 1,231,180 |
| Validation | 1661 | 1,423 | 172,101 |
| Test | 345 | 2,087 | 252,444 |
| Total | 8 | 13,681 | 1,655,725 |

Sequences stay within source chunks. Padding is masked, and each chunk's first
token has no preceding-token target. Thus target coverage equals 1,657,163 minus
1,438. Disjoint book IDs do not prove absence of shared boilerplate or exclude
these books from the imported model's historical pretraining.

The GPT-2 parent checkpoint contains 548,105,171 bytes in 33 hash-checked blocks.
Its reconstructed whole-file hash is:

`248dfc3911869ec493c76e65bf2fcf7f615828b0254c12b473182f0f81d3a707`

The weight ledger records an **import**, with `training_history_verified: false`.
The finalized training plan links this parent to the released dataset and a
next-token recipe; `executed` remains false. Trainer execution, evaluation,
checkpoint acceptance and weight promotion still need implementation.

## Reproducibility pins and evidence

| Item | Pin |
|---|---|
| Chain ID | `dawl-c6c39febb700b476` |
| Genesis SHA-256 | `bc10cc0c7ebdd02e5e29cff582b86522e50348a1d74f16caeef2b1d1d52c8843` |
| Plan SHA-256 | `0fc7d15c8521b520a47adc7ff7694229e763c023264d845c1e3a273733e7cc5f` |
| Python implementation SHA-256 | `bd14dfb8e1c1c82141fdff4d1a44962eab5ce1899fc49ba72385aceb224c22a6` |
| Executed source commit | `8f8a7eebb4a5d18ba972dac2112aca4f478a0a41` |
| Runtime image ID | `357dacd66bd08597a86c2d64d2d8fbbc3e87d25c99ab76b8427ea65b22fef96e` |
| Dataset release ID | `7df7e1ca7411840cade9368f6fe94d995f4faa98c02f67ab0908ea902f8c9a67` |
| Training plan ID | `3eec1f8ad46612f4c142183540904b8f4dd085381583d1bb4ec16a42509f9712` |

The public evidence lives in [results/cluster-run-002](results/cluster-run-002/).
Its [artifact index](results/cluster-run-002/artifact-index.json) hashes the saved
phase observations, genesis, plan, certificate, loader results and test output.
It contains no private keys or live databases. The index detects changes against
a separately retained copy; it is not itself a signed trust anchor.

An earlier successful deployment is preserved in
[results/cluster-run-001](results/cluster-run-001/). Run 002 repeats the exercise
after malformed-transaction handling, idle-worker behaviour and process shutdown
were hardened. The first chain's stopped containers and state remain preserved;
its keys and signing history were not reused to bootstrap the new chain.

Current scale limits include static admission, whole-state hashing and storage,
fixed provider copies, and operation on one machine. No permissionless membership,
Sybil resistance, incentive system or independently operated deployment is claimed.
