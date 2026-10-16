# Consumer evidence network: first live trial

2026-10-15. Two signed discovery peers and a separate CPU Evidence Cortex served
the current Seed III corpus. A consumer requested evidence for a real checkpoint
1500 continuation and two explicitly constructed retrieval controls. The original
continuation was preserved while the retrieval policy was refined.

## Corpus and services

* Blueprint: `4b7331add9246e4c966cbb67478687755f9daf5bfe5c19124effcb41affb7c63`.
* Training split: **145,011 documents, 597,868 passages**. Validation/test sources
  were excluded from the index and source-service allowlist.
* SQLite index: **1,185,742,848 bytes**, built in **411.55 seconds**, with the
  indexer limited to two CPU cores. Build timing excludes initial manifest loading.
* Search/provider service: `127.80.0.13:8914`; discovery peers:
  `127.80.0.13:8913` and `127.80.0.14:8913`.
* Provider/index/corpus mounts are read-only. Each discovery peer can write only
  its own peer state. The provider can read only its own signing identity.

The immutable index and raw sources remain in `/var/lib/dawl/evidence-001` and
the existing native Seed III corpus. The service snapshot is `runtime-v2`.
The trainer's frozen source, recipe and GPU process were not changed.

## Observations

The first broad OR query over the repetitive continuation hit the three-second
search budget. Its collection correctly recorded one unavailable/invalid search
and zero verified candidates. The initial record is preserved in
[initial-trial.json](initial-trial.json).

Query policy 2 removes a fixed English/Norwegian function-word list, deduplicates
terms and limits the query to twelve content terms. It reuses the same immutable
passage index. The rerun used the exact saved model output and checkpoint.

| Input | Verified candidate passages | Failed peers | Collection time | Source bytes received |
|---|---:|---:|---:|---:|
| Actual Seed III continuation | 3 | 0 | 0.503 s | 25,228 |
| Constructed photosynthesis control | 3 | 0 | 0.144 s | 24,934 |
| Deliberately false moonlight-to-gold control | 3 | 0 | 0.084 s | 12,313 |

These timings cover search, source transfer, hashing and collection construction
after discovery/manifest loading. They are three local observations, not a WAN
or sustained-load benchmark. Cache state can affect the comparison.

Every candidate has `semantic_support: not_assessed` and `confidence: null`.
Both a correct-looking control and a deliberately false control find lexical
neighbors. This demonstrates why candidate retrieval and claim assessment must
remain separate. No semantic accuracy result or calibrated confidence is claimed.

The model host returned checkpoint
`176e86471aad6680ffd1b5f0ecbb726e72add9b6c4085a52af26de99802b57c0`.
Its output and generation settings are preserved in [generation.json](generation.json).
The collection's model association is `consumer_asserted`; this trial does not
add a signed inference receipt to the model host.

## Independent process check

A fresh container with networking disabled, read-only public inputs and no
provider/consumer signing keys verified all three collections and all **nine
passage checks**. It independently checked:

* Consumer and nested provider signatures.
* Binding to the exact answer, claim spans, queries, nonces and Blueprint.
* Downloaded-document identities against the committed source manifest.
* Exact UTF-8 source excerpts at the recorded Unicode offsets.
* Reconstructed provenance fields and the absence of a semantic verdict.

See [offline-verification.json](offline-verification.json). This is a separate
process check on the same host/operator; it is not a multi-operator trust trial.

Fifteen relevant automated tests passed: seven evidence tests, two lab transport
tests and six console/accounting tests. They include excerpt/source tampering,
wrong Blueprint/query/nonce, stale replies, unadmitted discovery keys, wrong
profile capabilities, source-split isolation, bounded requests, an unavailable
peer with an honest fallback, and rejection of a fabricated support verdict.

## Inspect and reproduce

[evidence.json](evidence.json) pins the exact bytes of eleven exported public
records and the provider source files. Full source objects are required to repeat
the offline checks; the export does not duplicate the entire corpus or index.
The 52,145-byte record bundle includes source attribution and rights metadata
inside its receipts, plus explicitly labeled control inputs.

From the repository's WSL environment, with `src` on `PYTHONPATH`:

```sh
sh scripts/evidence_trial.sh
python -P scripts/evidence_live_trial.py --output /var/lib/dawl/evidence-001/trial-v3 \
  --generation /var/lib/dawl/evidence-001/trial-v2/generation.json
sh scripts/evidence_verify_trial.sh
```

The last command checks the preserved `trial-v2` in an isolated container. Use
the general `dawl_evidence verify` command for another collection. A fresh trial
output directory prevents replacing the earlier records.

The [architecture and next experiments](../../evidence_network.md) cover local
versus delegated assessment, calibrated support scores, training-exposure checks,
authenticated inference records and future deployment hardening. The participant
study and current model studio were not modified by this trial.
