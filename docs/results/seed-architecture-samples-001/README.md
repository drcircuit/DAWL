# Seed architecture samples 001: original 32-token continuations

Four unchanged studio outputs recorded on 16 October 2026 with seed 42,
temperature 0.7 and 32 new tokens. The common prompt was
`The traveller looked up at the stars and`.

[samples.json](samples.json) preserves exact requests, responses, sampler settings
and checkpoint identities. Seed I dense, Seed I MoE and Seed II use top-k 40;
Seed III uses top-p 0.9 and repetition penalty 1.1. This is an illustration of
the four configurations, not a controlled quality comparison.

The Investigator assessed each complete prompt plus continuation as one span
against the same Seed III training corpus. It returned insufficient evidence
for three outputs and flagged a contradiction for Seed I dense against *The
Enchanted April*. The scenes are unrelated fiction; that flag is an evaluator
diagnostic, not an established factual contradiction.

All 12 retrieved passages passed source hash, excerpt and Blueprint membership
verification. [investigations.json](investigations.json) contains origin links,
full passages, scores and assessment identities. Each `example-N-collection.json`
and `example-N-investigation.json` is the original signed record.

[semantic-replay.json](semantic-replay.json) records fresh-process offline CPU
replay of all 12 passage judgments: exact raw outputs, unchanged verdicts and
maximum score difference 0.0. This is the same hardware/runtime cohort, not
independent-operator validation. The checkpoint binding in the collections is
a consumer assertion; source membership does not prove causal training influence.

The later [128-token experiment](../seed-architecture-samples-002/README.md)
preserves this bundle and changes the assessment unit to sentence spans.
