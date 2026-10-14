# Protocol 3: complete six-profile local cluster run

Completed on **14 October 2026 at 02:22 UTC**. Four CometBFT Oracle containers
certified **78 completed book/profile jobs, 156 signed audit receipts and six
independent Blueprint heads**. Two CPU Daemons each replayed every book under
every profile. The Cortex and its DHT discovery service ran separately.

All six profiles covered the same **13 books, 7,355,184 raw source bytes and
1,807 Frags**. This represents 10,842 distinct profile/Frag combinations and
21,684 Frag replay executions across the two auditors. No profile audit reported
an invalid result or other failed outcome. This is token provenance and replay;
it makes no claim about new vector reconstruction or model training.

| Profile | Tokens | Fewer than GPT-2 | Certified receipts |
|---|---:|---:|---:|
| GPT-2 baseline | 2,061,330 | — | 26 |
| SmolLM2 | 1,933,422 | 6.21% | 26 |
| Qwen3 | 1,763,536 | 14.45% | 26 |
| Qwen3.5 | 1,780,226 | 13.64% | 26 |
| SmolLM3 | 1,761,415 | 14.55% | 26 |
| o200k_base | 1,753,309 | 14.94% | 26 |

Token counts matched the earlier local corpus comparisons exactly. The profiles
use their declared normalization and independent 4 KiB Frag contracts. These
numbers compare tokenization on this corpus, not model quality. `o200k_base`
remains a tokenizer-only profile, with no borrowed embedding matrix or fabricated
model dimensions.

## Faults and engineering findings

One validator was deliberately stopped. Between the saved checkpoints, the chain
advanced from height **163 to 225**, accepting **eight additional receipts** with
three of four validators. The fourth validator was then restarted.

Both auditors later exceeded their initial **1 GiB** container memory ceilings.
They restarted with retained identities, caches and durable outboxes at **3 GiB**
ceilings. The chain recorded **two expired leases**; the auditors completed their
remaining work without duplicating their prior attestations. This is evidence of
recovery under that observed failure, not a general claim of crash-proof operation.
The local runner now gives Daemons 3 GiB and other services 1 GiB ceilings. Small
nodes should advertise only profiles they can execute within their resources.

An earlier [interrupted probe](../profile-testnet-001/README.md) exposed excessive
disk commits for transfer telemetry. The successful run uses bounded batching
for counters and recent events. Signed outbox transactions and consensus receipts
remain on their original durable paths. The timings are uncontrolled and include
I/O, cache checks, provenance verification, tokenization and certificate queries;
this is not a WAN throughput or tokenizer-speed benchmark.

## Evidence and verification

- [Report](report.json): six release IDs, counts and implementation hash at height 2086.
- [Release certificate](release-certificate.json): quorum certificate matching that report's state hash.
- [Independent final capture](completed-certificate.json): a later certified state at height 2235 with the same six heads and 156 receipts.
- [Public evidence](public-evidence.json): genesis, all 156 individually signed audit transactions and six signed lineage bundles.
- [Genesis](genesis.json): public validator keys and the new chain's bootstrap plan.
- [Implementation pins](implementation.json): exact container image ID, Python source hashes and reproduction-script hash.
- [Before outage](before-outage-certificate.json) and [during outage](during-outage-certificate.json): saved public certificates.
- [Verification record](verification.json): signature, coverage, quorum and regression-check results.

The online clients verified certificates against the independently pinned genesis.
The standalone Go checker subsequently verified the exported quorum offline using
CometBFT's own validation functions. Adversarial checks rejected changed state,
forged vote signatures, an incorrect genesis pin and insufficient voting power
(two validators). The Python checker verified every individual audit signature,
its profile/job binding, Blueprint receipt coverage and all six lineage bundles.

```powershell
.\.venv\Scripts\python.exe scripts/verify_profile_testnet.py
```

To verify the historical quorum independently, run the Go checker from `consensus`
with the genesis hash you obtained when joining this pilot:

```sh
go run ./cmd/check-certificate --genesis ../docs/results/profile-testnet-002/genesis.json --genesis-sha256 8a3f05580926f5ca9d0c5c87f4c7e9334a93d9e1e3145cb2eb2be624a6097dc6 --certificate ../docs/results/profile-testnet-002/completed-certificate.json
```

The complete Python regression run executed **185 tests: 183 passed, two skipped**.
The skips require the separate CUDA test container; this request did not exercise
new GPU/vector recipes. The exported Go certificate checks are additional to that
Python count. Core implementation digest:
`0f961b9bdb1be5a31127ff43bcd833146520ad1cb3f7a43c25870a759842a98b`.

## Scope

This was one local host and one operator, using separate containers and genuine
CometBFT ordering/certificates. Different signer keys do not establish independent
human operators or prove that computation happened. The raw corpus and tokenizer
assets are needed to repeat the full replay; signature verification alone is not
retokenization.

The v1/v2 bootstrap used a labelled synthetic one-token fixture solely to exercise
the version transition. It is excluded from all v3 counts above and is not evidence
of vector reconstruction. The v3 jobs used the actual 13-book corpus. No live
validator identities or receipts were copied from the hybrid testnet. Azure was
not changed. See the [protocol and reproduction guide](../../profile_protocol_v3.md).
