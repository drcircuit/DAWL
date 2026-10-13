# Stage 1 — first experimental results

Recorded on **9 October 2026**, using Python 3.12.14 on Windows 11.
This is an initial local experiment, not a peer-reviewed result or a training
network benchmark. The complete reference output is in
[stage_1_results.json](stage_1_results.json).

## Reproduction

```console
python -m unittest discover -s tests -v
python dawl.py --output artifacts/reference-reproduction
```

The default corpus is defined literally in `src/data_ingestion/experiment.py`.
It contains 136,192 Unicode scalar characters, all ASCII for the reference run.
The embedding definition has dimension 128 and seed `dawl-stage-1`.
The manifest's stable block identifier is:

```text
1b520e82bf54975f9a472dffa69a9d919b136ee1b48dd0b6a1a0cdb7c8a19d4b
```

Byte counts and the block identifier should reproduce exactly. Audit positions,
response size, and timings may differ because the validator uses fresh randomness.
The reference timing run was executed separately after the test suite completed.

## H1.1: representation and deterministic recovery

| Representation | Exact bytes |
| --- | ---: |
| Raw UTF-8 source | 136,192 |
| Materialized float32 embedding rows | 69,730,304 |
| uint32 token payload | 544,768 |
| Canonical block manifest | 596 |
| Published payload plus manifest | 545,364 |
| Miner-local cached Merkle digests | 17,432,896 |

Payload savings relative to dense embeddings: **99.21875%**.
Published-object savings, including the manifest: **99.21789527835702%**.
Published bytes are **4.004376 times** the raw UTF-8 source size. This is the
expected tradeoff for four-byte character tokens, not compression of raw text.
Digest-cache bytes exclude Python objects and the retained source; they are not
included in published bytes. No total-system 99% saving is claimed.

The separate Unicode test corpus contains 8,960 tokens in 10,496 UTF-8 bytes.
Its dense representation is 4,587,520 bytes; the token file and manifest total
36,423 bytes, a **99.20604160853794%** saving against that dense baseline.
Reconstructing its embedding rows produces identical bytes. Tests also demonstrate
that the >99% published-object claim fails for very small blocks and for dimension
64 with four-byte tokens.

## H1.2: spot checks and cost

The reference run uses **64 distinct random positions** per challenge and five
complete honest audits. All five pass. The main phases were:

| Phase | Observed milliseconds |
| --- | ---: |
| Independently registering the source | 536.3436 |
| Miner ingestion and both commitment trees | 1,063.5690 |
| Materializing dense embedding bytes | 67.6362 |
| Verifying the full token payload | 564.9793 |
| Complete 64-position audit, median of five | 9.4004 |

The median audit is **0.883854%** of the measured ingestion time for this fixture.
This does not establish a universal <1% result. The measurement includes challenge
creation, miner response construction, and validator verification, with source
registration, cached-tree construction, full-file verification, network, disk,
and response JSON serialization outside the audit timer. Dense materialization
is not included in the ingestion denominator. The last JSON audit response is
159,741 bytes, larger than this fixture's raw source; per-character proofs are
intentionally simple and communication efficiency remains an optimization target.

Under fixed corruption and uniform sampling without replacement:

| Corruption scenario | Detection probability for 64 samples |
| --- | ---: |
| One corrupted token out of 136,192 | 0.0469924812% |
| 1,361 corrupted tokens, approximately 1% | 47.42525237% |
| Every token inconsistent with the committed source | 100% in the idealized model |

The malicious-miner test commits all 53,248 token positions incorrectly **before**
the challenge is drawn. Its Merkle proofs are valid for the committed malicious
token file; all 64 sampled answers fail because they disagree with independently
committed source characters. This exercises source/token validation rather than
merely noticing a broken file hash. The test logs its own timings but does not
assert a hardware-dependent runtime threshold.

For sparse corruption, another test enumerates all 21 two-position samples of a
seven-token block with two fixed errors. Exactly 11 samples detect corruption,
matching `1 - C(5,2)/C(7,2) = 11/21`. An explicit missed-error test passes an audit
whose selected positions are honest. This is the intended probabilistic behavior.

## Validation status and scope

**32 automated tests pass.** Coverage includes cross-process determinism under
different Python hash seeds, Unicode and newline preservation, empty/invalid
input, canonical serialization, odd-sized Merkle trees, index binding, tampered
payloads, forged proofs, source substitution, stale responses, malformed evidence,
publication, and the command-line path.

These results establish the local Stage 1 experiment. They do not demonstrate
decentralized training performance or prove the complete DAWL architecture.
The larger research direction and decisions deferred to later stages are recorded
in the [paper and roadmap](stage_1_data_ingestion.md).
