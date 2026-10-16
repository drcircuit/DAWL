# The Hypertrix: names and technical meaning

These names describe the current prototype and its intended platform. Technical
contracts, signatures and historical evidence must still identify the exact
operation performed. A name does not strengthen the evidence behind a claim.

| Name | Meaning | Current implementation |
|---|---|---|
| **The Hypertrix** | The overarching open data and model platform | DAWL research code and the hybrid pilot |
| **The Collective** | The network of participating nodes | Explicitly admitted peers connected across two sites |
| **The Oracle** | The consensus validator ring | Four equal-weight CometBFT validators; at least three votes to commit |
| **Cortex Units** | Persistent artifact providers | Content-addressed source bytes, tokens, tokenizer assets and the embedding table |
| **Daemons** | CPU replay auditors | Independently fetch, verify and reconstruct assigned inputs; sign audit results |
| **Investigators** | Evidence assessment agents that examine claims, sources and their relationships | `EVIDENCE_ASSESSOR` service; pinned CPU NLI, verified passages, signed judgments and replay; calibration and broader semantic validation remain open |
| **The Hypertrix Console** | The live observability interface | Read-only dashboard on localhost:8882; no signing keys |
| **Frags** | Discrete source/token chunks | Chunk IDs, token positions and content-addressed artifacts |
| **Synapses** | Reconstructed input embeddings | Token-indexed, 768-dimensional GPT-2 input embeddings; not semantic document vectors |
| **The Source Code Matrix** | The shared embedding table | Pinned imported GPT-2 matrix; not source code or a learned update from this pilot |
| **Cipher-Logs** | Signed provenance records and audit receipts | Signatures authenticate authors and exact bytes; they do not encrypt records or prove execution |
| **Execution Leases** | Time-bounded audit assignments | Claims, renewals, outcomes and expiry ordered by The Oracle |
| **The Blueprint** | A released dataset plan | Corpus, replay coverage and artifact commitments ready for verified consumption |

The Oracle certifies an agreed ledger state. It does not establish semantic truth,
copyright status, data quality, or the honesty of every audit claim. Likewise,
The Blueprint is a dataset release; this pilot does not train or release model weights.
Daemons keep a small durable transaction outbox and sequence state even though
their replay work is independent of providers.

## Fresh experimental network

New topology identities are named `oracle-0` through `oracle-3`, `cortex-0` and
`cortex-1`, `daemon-0` through `daemon-19`, and `blueprint-publisher`.
The Hypertrix pilot creates fresh node keys, admission registrations, genesis,
Execution Leases, audit Cipher-Logs and a Blueprint. Earlier DAWL runs remain
separate experimental evidence.

The public-domain input files and their original ingestion lineage are reused as
pinned historical inputs. They are not relabelled as newly ingested material or
silently re-signed by their old authors. Protocol schema names, Python packages,
CLI names, the repository directory and Azure resource group retain `dawl`/`DAWL`.
This makes archived records and reproduction commands unambiguous without making
backward compatibility a constraint on future experiments.

Future changes to signed payloads require new signatures and fresh network pins.
We can start another experimental chain when the research requires it; we must
not rewind a used validator's signing state or reuse its identity on a conflicting
history within the same chain.
