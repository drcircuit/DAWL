# Seed architecture samples 002: longer continuations

Recorded on 16 October 2026 after the host restart. Each of the four selected
models produced one unedited continuation with **seed 42, temperature 0.7 and
128 new tokens**. The prompt is identical:

> The traveller looked up at the stars and

The studios and Investigator ran on CPU. The GPU trainer remained stopped.
The first three models use top-k 40; Seed III uses top-p 0.9 and repetition
penalty 1.1. These are illustrative samples, not a controlled quality benchmark.

## Checkpoints and observed results

| Model | Checkpoint step | Exact spans | Passage comparisons | Span verdicts | Assessment seconds |
|---|---:|---:|---:|---|---:|
| Seed I dense | 5,844 | 6 | 18 | All insufficient evidence | 20.51 |
| Seed I MoE | 2,900 | 4 | 9 | All insufficient evidence | 11.27 |
| Seed II | 7,500 | 4 | 12 | All insufficient evidence | 15.34 |
| Seed III | 3,750 | 5 | 15 | All insufficient evidence | 19.86 |

Every output reached 128 tokens without prompt truncation. Seed III loaded its
selected checkpoint at update 3,750; the recovery checkpoint at 5,500 is not the
checkpoint used here. Full SHA-256 identities are in [samples.json](samples.json).

The client splits exact prompt-plus-continuation text at deterministic sentence
boundaries and retains the unfinished final span. This is segmentation, not
claim rewriting or factual-claim extraction. It found **19 spans**. One MoE
span had no retrieved candidate; the other 18 each had three, giving **54
passage comparisons**. There were no retrieval failures or NLI context-limit
abstentions. All 19 aggregate labels and all 54 passage labels were insufficient
evidence. Support, contradiction and conflicting evidence did not occur.

No outputs, source passages or thresholds were changed to fill missing labels.
No synthetic outputs were added. Fictional continuations may lack supporting
sources even when coherent; poor generation, limited retrieval and evaluator
quality can each affect this result. It is not a model-accuracy measurement.

## Auditable evidence

The shared reference pool is the Seed III training corpus: 145,011 documents
and 597,868 indexed passages. It is used for all four models; this does not
establish that an earlier model trained on these exact source versions.

- [samples.json](samples.json): original request, response, checkpoint and sampler.
- [investigations.json](investigations.json): exact spans, origin links, complete
  retrieved passages, raw scores and receipt identities.
- `example-N-collection.json`: original signed collection, source responses and
  exact character offsets.
- `example-N-investigation.json`: original signed Investigator receipt.
- [semantic-replay.json](semantic-replay.json): separate-process verification.

All source hashes, exact excerpts, Blueprint membership, answer bindings and
signatures passed. An offline CPU process with no signing keys reproduced all
54 raw NLI outputs and verdicts exactly, with maximum score difference **0.0**.
The verification/inference loop took **67.09 seconds**, excluding evaluator
loading. It used the same hardware/runtime cohort, not an independent operator.
Checkpoint binding in a collection is a consumer assertion; this replay checks
the Investigator and does not repeat model generation or training.

Evaluator identity:
`393a55600b01003bba51a6a532156ddd6c7ef917f39b3b5a165f50c959f8a15a`.
Its policy is unchanged: support/contradiction score at least 0.90 and a margin
of at least 0.20; otherwise abstain. Raw scores are not calibrated confidence.
See [Investigator 001](../investigator-001/README.md) for the model, revision,
manifest, policy and diagnostic results.

## Reproduction

Use a fresh output directory to preserve these observations:

```sh
python scripts/seed_architecture_samples.py --output /path/to/new-samples --tokens 128
python scripts/seed_architecture_investigate.py --examples /path/to/new-samples --sentences
```

These commands require the configured local studios, admitted source provider,
Investigator and the recorded Blueprint. `scripts/investigator_replay.py` runs
against saved records with local corpus/model files; no service or network is
required. Use the evaluator's frozen runtime to match its implementation digest.

The PDF lists every candidate with its judgment and source-origin link. For
readability it quotes the first candidate per span and abbreviates identifiers;
the bundle preserves every full passage and full digest. The earlier
[32-token run](../seed-architecture-samples-001/README.md) is intact. Both the
output length and the assessment granularity changed, so this is not a
controlled test of length alone.
