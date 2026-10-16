# Investigator 001: semantic assessment and measured limitations

On 16 October 2026 a separate CPU Investigator joined the local discovery mesh,
fetched committed sources from the Evidence Cortex and returned signed NLI
judgments for unchanged outputs. This is an implemented post-generation
assessment path. It does not revise the model's answer or inject source text
into its prompt.

The model is [mDeBERTa-v3-base-mnli-xnli](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli),
revision `8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c` (MIT declared model card).
The local [manifest](model-manifest.json) pins the safetensors, tokenizer and
configuration. CPU FP32 inference uses the recorded runtime and two threads;
the service is capped at two CPUs and 4 GiB. It receives no GPU device.

This is **ALCE-inspired diagnostic evaluation**, not the official ALCE benchmark
or its AutoAIS evaluator. [ALCE](https://aclanthology.org/2023.emnlp-main.398/)
and its [evaluation implementation](https://github.com/princeton-nlp/ALCE/blob/main/eval.py)
motivate measuring citation support and coverage separately. Our test uses
single claim/passage pairs and a different evaluator. It does not reproduce
ALCE's joint-citation necessity evaluation or datasets. RARR remains a research
reference; its answer-revision stage is not implemented.

## Frozen policy and network path

- Support: entailment score >= 0.90 and at least 0.20 above either alternative.
- Contradiction: the analogous contradiction threshold and margin.
- Otherwise: insufficient evidence. Opposing qualifying passages produce a
  conflicting result. No candidates also means insufficient evidence.
- A pair over 512 tokenizer tokens abstains intact. No silent truncation.
- No calibrated confidence is emitted. Raw softmax outputs are recorded.
- The current evaluator supplies a templated policy explanation, not a generated
  reasoning trace, source-reliability judgment or proof of training influence.

Discovery uses peer schema 4, a signed `EVIDENCE_ASSESSOR` endpoint and pinned
Blueprint/evaluator/policy IDs. Consumer admission is separate from discovery.
The service verifies a signed, fresh consumer request and rederives source
hashes, excerpts and membership before inference. Signed judgments bind the
original collection and exact answer hash. Source transfers use the configured
private-network Cortex endpoint; requests cannot supply arbitrary URLs.

The new peers are `127.80.0.15:8913` (Investigator discovery),
`127.80.0.15:8915` (assessment) and `127.80.0.16:8913` (compatible discovery
bridge). The existing provider remains `127.80.0.13:8914`. No running trainer,
source provider or prior mesh peer was restarted. The active GPU recipe was
unchanged. Service access logs and automatic prompt persistence are disabled;
the trial client explicitly saved these public examples.

## Draft diagnostic: 48 pairs

[Fixtures](fixtures.json) contain 16 source groups, each with three draft labels:
entailment, contradiction and neutral. Eight groups are development-only and
eight are held out for testing, yielding 24 test pairs (12 English, 12 Norwegian).
The labels were authored by the coding agent and need independent review.
They are diagnostics, not a validated estimate of deployment accuracy.

| Held-out measure | Observed result |
|---|---:|
| Agreement with draft three-way labels | 22 / 24 |
| Selected support citations matching draft labels | 7 / 7 |
| Draft-supported cases accepted | 7 / 8 |
| Draft-nonsupported cases accepted as supported | 0 / 16 |
| Draft contradictions detected | 8 / 8 |
| English label agreement | 11 / 12 |
| Norwegian label agreement | 11 / 12 |

The disagreements were `samtykke-no/0` (abstention on a consent requirement)
and `trial-en/2` (contradiction instead of the draft neutral label). The latter
asserts that a study established a result for children despite explicitly
excluding children; its label itself merits review. Neither case nor the
thresholds were altered after seeing the test results. Norwegian was evaluated
separately; multilingual pretraining does not establish Norwegian NLI quality.

Loading took 37.03 seconds; evaluating all 48 pairs took 31.01 seconds. A
development-only temperature grid selected 1.5. On test, multiclass Brier
changed from 0.1537 to 0.1394, while entailment ECE worsened from 0.0249 to
0.0527. These small draft-data measurements do not authorize production
confidence. All scores and bins are retained in [evaluation.json](evaluation.json).

## Real retrieved passages expose a quality gap

The three original collections were assessed without changing their text or
retrieval results:

| Sample | Verdict | CPU service elapsed |
|---|---|---:|
| Actual Seed III photosynthesis continuation | Insufficient evidence | 10.84 s |
| Positive photosynthesis control | Insufficient evidence | 5.89 s |
| False moonlight-to-gold control | Insufficient evidence | 6.77 s |

The positive control is a real false negative under the declared policy. Its
Simple Wikipedia passage literally contains the supporting statement, but
whole-passage NLI returned entailment 0.320; another passage returned 0.811,
below 0.90. This exposes a gap between short constructed pairs and the actual
1,200-character retrieved passages. Thresholds were preserved and the failure
was recorded. The next measured comparison should include smaller evidence
units with surrounding context, retrieval relevance, atomic-claim selection
and a stronger entailment evaluator, with a fresh held-out set.

The four [architecture examples](../seed-architecture-samples-001/README.md)
add 12 real passage judgments. Three full fictional fragments abstained; Seed I
dense was flagged as contradictory to a passage from *The Enchanted April*.
These are unrelated fictional scenes, so that flag is an evaluator diagnostic,
not an established factual contradiction. Well-formedness and applicability
gates are needed before using this evaluator for general output-quality scores.

The [128-token follow-up](../seed-architecture-samples-002/README.md) assessed
19 exact sentence spans from the same four selected checkpoints, including
unfinished endings. All 19 spans and all 54 retrieved passage comparisons
received insufficient evidence; one span had no candidate. No thresholds or
outputs were adjusted to fill absent labels. Both length and assessment
granularity changed, so the two runs do not isolate a length effect.

Fresh-process CPU replay with networking disabled and no signing keys reproduced
all 12 original and all 54 follow-up raw NLI outputs and verdicts exactly
(maximum absolute score difference 0.0). The later loop took 67.09 seconds,
excluding loading. It checks the same runtime/hardware cohort; it is not
independent-operator validation or a replay of the generative model.

## Reproduction and checks

The evaluator identity is
`393a55600b01003bba51a6a532156ddd6c7ef917f39b3b5a165f50c959f8a15a`.
The model manifest is
`811ca0d441e754022ded0e84f8952fa5d8e6379b2f1e354a8caeb0992feee8bb`.

Commands are in `scripts/investigator_prepare.py`, `investigator_evaluate.py`,
`investigator_trial.sh`, `investigator_live_trial.py` and `investigator_replay.py`.
The downloader accepts a fixed revision and allowlisted files. Runtime loading
is local-only, safetensors-only, with remote code disabled. Public metadata
export uses an explicit allowlist and contains no node private keys.

Thirteen evidence/Investigator tests, two lab transport tests and six monitor
tests passed. They exercise signature and binding tampering, source corruption,
admission, nonce replay, score validation, context abstention, conflicting
judgments, DHT capability pins and split leakage. The two transport tests require
the lab container's tokenizer dependencies. The actual model diagnostic is
reported separately; unit-test success is not semantic-quality validation.
