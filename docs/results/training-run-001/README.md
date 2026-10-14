# Distributed training run 001

**Completed:** two separate training peers continued the real pinned SmolLM2-135M
model, contributed 64 signed adapter updates,
independently computed 32 matching checkpoints and published the
same audited stage release. A separate auditor reproduced all aggregation and
replayed two post-commit selected training contributions byte-for-byte.

The [architecture and protocol](../../distributed_training.md) document the
stage lifecycle, mathematical contract, recovery rules and future work.

## Measured work

| Measurement | Result |
|---|---:|
| Training peers | 2 |
| Physical GPUs | 1 shared RTX 5090 Laptop GPU |
| Source books | 13 |
| Training / validation / test books | 11 / 1 / 1 |
| Training rounds | 32 |
| Local optimizer steps per peer | 128 |
| Distinct allocated training windows | 512 |
| Actual next-token training targets | 62,696 |
| Updated trainable parameters | 184,320 |
| FP32 adapter payload per contribution | 737,280 bytes |
| Total peer-to-peer adapter payload | 47,185,920 bytes |
| Peer 0 / peer 1 local-training time | 65.28 / 48.53 s |
| Changed shared checkpoints | 32 |

This is a bounded partial corpus pass. It is not a full epoch or full-model
fine-tune: the pretrained base and A projections stayed frozen, and B adapters
in all query/value attention projections were trained. Times above sum the
reported local optimizer work and exclude input retrieval, evaluation, peer
waiting and the intentional interruption. The two GPU containers contend for
one device, so these timings do not measure network scaling.

Both peers independently pulled their initial artifacts into their own stores.
Peer 0 received 323,244,478 bytes of successful object bodies;
peer 1 received 323,244,478. These totals include initial model,
source/token provenance and updates. The original BF16 pretrained checkpoint was
269,060,552 bytes; repeated training rounds exchanged the small
FP32 adapters. The adapter total is payload, excluding signatures, HTTP framing,
polling, startup downloads and auditing. No WAN compression measurement is implied.

## Recovery and independent publication

Peer 0 deliberately exited after writing its round-1 contribution. Peer 1 could
not finalize that round without the missing checkpoint acceptance. On restart,
peer 0 reused its two durable contributions and re-verified the previous round,
then both peers completed the same parent-linked history. It reused
2 updates in the resumed process.
The exported process logs record the interruption and subsequent progress.

The original Cortex was stopped before the cold auditor started. The auditor
received 370,430,398 object bytes from the training replicas,
including the base model, data and all adapter candidates. Its recorded origins
were http://127.70.0.31:8910. Both trainers retain complete
publishable composite checkpoints and identical weight ledgers. There is no
designated sole weight publisher.

After release, peer 0 was also stopped. A fresh public consumer then downloaded
and verified the complete parent model, fixed projection, trained adapter,
configuration and tokenizer using **only peer 1**: 273,009,012
bytes in 37 objects. See [availability.json](availability.json).
Together, these two cold downloads exercise both training peers as independent
artifact publishers. All temporary pilot services were stopped after the checks;
their native state and public evidence remain available for restart.

The auditor verified every signature, work allocation, parent link, replica and
weighted aggregate. It then selected two contributions using a fresh nonce and
the already committed final head. Both full local training replays matched the
original adapter hashes exactly. Those samples are recorded in
[audit.json](audit.json); they do not prove all 64 training claims.
Only after this audit did both peers sign the [stage release](release.json).

## Evaluation

Each measurement uses the same 16 pinned windows per split. Loss is mean
next-token cross entropy over valid targets. The auditor independently reproduced
both peers' final evaluation exactly.

| Split | Base loss | Final loss | Evaluated targets |
|---|---:|---:|---:|
| train | 3.267120 | 3.221374 | 2,048 |
| validation | 3.246371 | 3.204986 | 1,870 |
| test | 3.266384 | 3.233309 | 1,896 |

Validation book 1342 and test book 84 were excluded from all training allocations.
The parent model may already have encountered these public books during its
pretraining. These limited measurements check execution and detect gross regressions;
they do not establish broad model improvement or downstream task quality.

## Provenance and acceptance boundaries

- Stage: `64493100f2d77352e621a74086937458ec8a5ed644ab8754735c9bfa3cae9c91`.
- Token Blueprint: `cf809d74c0ad88cac99f9588f51a25378e3842221126a8ea7a6399b4da7013cc`.
- Imported parent checkpoint: `80521b40281d6ce74e35c9282c22539e75aa0ac8578892b2a59955ef78d55da1`.
- Source revision: `93efa2f097d58c2a74874c7e644dbc9b0cee75a2`.
- Final checkpoint: `17b0c416349efa5e996a418d13267620b977872561be1f335eaf0d678bc9f755`.
- Audited release: `a2a0c82faefd242e6e1b027c8f4645312e6f77fc473087b1ce6f071e0845da68`.
- Executed source digest: `c5ef779d4b706551d6685de63fdf06cb0bd397530686f1de769d731926c678f2`.

The source token Blueprint has the historical Oracle certificate. Each trainer
and the auditor separately checked that quorum and replayed source tokenization.
The new training history is a **local, unanimously signed branch**, with a pinned
auditor and a separate audited stage release. It has not been admitted into the
live Oracle chain. The two trainers and auditor belong to the same operator.

The common parent, recipe, runtime, deterministic work schedule, code identity,
per-contribution adapter, round aggregates, sampled replay and release signatures
are retained. This distinguishes a declared training effort from the work that
was independently replayed. Missing-peer liveness, dynamic admission, decentralized
audit randomness, stale updates, poisoning resistance and wider hardware numerical
tolerances remain open. See the architecture document for the next scaling tests.

## Verify and reproduce

```powershell
.venv\Scripts\python.exe scripts/verify_training.py
```

This offline check verifies signed claims, complete coverage of the announced
stage and all parent/release bindings. It does not execute tensor operations.
The numerical audit was performed by `training_peer.py audit`, using a cold
separate node. Public evidence is in [summary.json](summary.json),
[chain.json](chain.json), [updates.json](updates.json), [completions.json](completions.json),
[verification.json](verification.json) and the signed audit/release.
The native content-addressed stores remain under
`/var/lib/dawl/training-001/state`; private keys are not exported.

The runbook in [distributed_training.md](../../distributed_training.md) gives the
image builds and lifecycle commands. A fresh run requires fresh directories and
identities. The final model is executable as the pinned base checkpoint plus the
fixed A and final B artifacts; it has not been packaged as a general chat model.
