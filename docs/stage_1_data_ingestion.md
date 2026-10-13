# DAWL Stage 1: Deterministic Data Ingestion and Spot-Check Audits

**Research prototype, version 0.1 — 9 October 2026**

## Abstract

The Decentralized Asynchronous Weight Ledger (DAWL) proposes an open infrastructure
for publishing, evaluating, and developing neural-network weights as a shared
resource: the project ambition is a "Linux of AI." This first experiment addresses a narrower prerequisite: reproducible
data ingestion, compact token storage, and verifiable sampled responses from a
data miner. We specify a deterministic toy tokenizer, reproducible embedding
lookup, content-addressed manifests, and an interactive Merkle-proof audit.
The implementation requires only Python's standard library.

Two claims from the initial manifesto are revised into falsifiable statements.
Replacing stored float32 embedding rows with uint32 token IDs saves 99.21875% of
the **payload** at an embedding dimension of 128. Whole-object savings depend
on metadata and block size. Sampled audits detect inconsistencies with an explicit
probability; they cannot guarantee detection of an arbitrary unsampled error.
The protocol discloses source snippets and therefore is not zero-knowledge.
Its timing is measured, not assumed to be below 1% of ingestion cost.

## 1. Vision and architectural boundaries

The long-term design is a directed acyclic graph of versioned model artifacts.
Branches may specialize; evaluation and usage may guide their adoption. Such a
graph can record conflicting proposals without imposing one global sequence of
weight updates. It does not by itself solve consensus, economic incentives,
Sybil resistance, data quality, or training convergence.

The organizing principle is independent participation: people should be able to
run the software, contribute data or hardware, inspect provenance, select a model
branch, and continue its development without one operator's permission. Incentives
are proposed as a way to sustain those contributions. The Bitcoin analogy motivates
coordination among independent participants; it is not an assumption that training
has the same computation or verification structure as hash-based work.

The candidate research contribution is the combined organization of independently
evolving branches, contribution provenance, user selection, and compatible merge
proposals. Its distinctiveness and value require evaluation. Stage 1 does not
claim that token storage, Merkle proofs, LoRA, or model branching are new inventions.
It establishes reusable interfaces while later mechanisms remain open.

The manifesto's distributed-interconnect proposal combines three research ideas:

1. **Pipeline partitioning.** Workers own contiguous layer groups and exchange
   forward activations and backward activation gradients. Overlapping independent
   microbatches can improve utilization, but each microbatch still has causal
   dependencies. Correct backward computation needs compatible forward state and
   parameter versions. PipeDream explicitly addresses this versioning problem
   [1]. Neither internet latency nor stage failures disappear.
2. **Low-rank adaptation.** A frozen pretrained model can be adapted through
   trainable low-rank matrices, reducing trainable parameter and optimizer-state
   requirements [2]. A frozen model is still read during forward/backward
   computation. Storing it on NVMe does not remove the need for a concrete memory
   and offload strategy. Small adapter files do not bound activation traffic, and
   this design initially concerns adaptation, not pretraining a new foundation
   model from scratch.
3. **Asynchronous optimization.** Delayed work may be useful under an explicitly
   specified optimizer, version policy, bounded delays, and measured convergence.
   A latency multiplier alone is not a general correction for stale gradients.
   Non-IID data, malicious workers, and arbitrary outages remain research risks.

For orientation, a boundary tensor with batch size B, sequence length S, hidden
width H, and p bytes per element costs B*S*H*p bytes in one direction. With
B=1, S=2048, H=8192, and p=2, that is 32 MiB. Sending it forward and a similarly
sized gradient backward costs approximately 64 MiB per boundary per microbatch,
before other protocol traffic. This arithmetic is independent of LoRA rank.

Likewise, cosine similarity of weight vectors is insufficient to establish
functional equivalence. Model soups are evidence that weight averaging can work
for compatible fine-tuned models [3], not a universal merge rule. For LoRA,
averaging A and B separately generally does not equal averaging their products
BA. Future merges must specify parameterization, provenance, compatibility, and
held-out evaluation. The phrase "wavefunction collapse" is only a metaphor.

Stage 1 implements no GPU training, DHT/IPFS network, token rewards, validator
committee, weight DAG, or branch merging. Its output is a local, content-addressed
artifact suitable for a later storage transport. Published data and commitments
are public; no confidentiality is provided.

## 2. Deterministic representation

### 2.1 Vocabulary and serialization

The toy vocabulary assigns each Unicode scalar value its numeric code point.
Each token corresponds to exactly one Python Unicode character, not a grapheme
cluster. UTF-8 decoding is strict, lone surrogates are rejected, and no case,
whitespace, newline, or Unicode normalization is performed. Combining characters
remain separate tokens. This choice makes snippet boundaries unambiguous and
avoids fitting a vocabulary separately on different miners.

It is deliberately not a competitive language-model tokenizer. A later BPE or
SentencePiece adapter must pin its vocabulary, model revision, special tokens,
normalization rules, and context needed to audit a snippet. Arbitrary fragments
cannot safely be re-tokenized without those boundary rules.

Tokens are serialized as unsigned 32-bit little-endian integers. The explicit
width is conservative for this toy vocabulary and compatible with larger model
vocabularies. JSON lists and Python object sizes are not used as payload sizes.

### 2.2 Embedding reconstruction

The local toy matrix is defined lazily by a versioned SHAKE-256 construction over
the embedding seed, dimension, and token ID. Each output uint16 u becomes
(u - 32768)/32768, exactly representable in IEEE-754 float32, then is packed
little-endian. Identical inputs produce identical row bytes. No learned semantic
information is claimed. The seed reconstructs this synthetic matrix only; it
cannot reconstruct arbitrary learned weights. Real deployments must pin and
distribute the actual embedding matrix/checkpoint.

Only input lookup rows can be reconstructed this way. Contextual hidden states,
attention outputs, gradients, and subsequent activations are not recoverable
from token IDs by a fixed lookup. If a real embedding matrix changes, its version
must be tracked. This is a representation baseline, not a new compression
algorithm or proof of decentralized training feasibility.

## 3. H1.1: Deterministic recovery and storage savings

Let N be token count, d embedding dimension, b=4 token bytes, f=4 float32 bytes,
and M the actual canonical manifest byte count. Then:

```text
dense_bytes       = N*d*f
token_bytes       = N*b
published_bytes   = N*b + M
payload_savings   = 1 - b/(d*f)
published_savings = 1 - (N*b + M)/(N*d*f)
```

For d=128, payload savings equal 1 - 4/512 = 99.21875%. Whole-object savings
exceed 99% only when M < 1.12*N. Tiny blocks may be larger than the dense
baseline; empty input has no defined savings ratio and is rejected as a block.
Increasing d changes the result; the implementation tests this boundary rather
than treating 99% as universal.

**H1.1, revised:** With a fixed lookup specification, reconstructed row bytes are
identical, and the exact whole-object savings follow the expression above.
The test corpus is large enough to test the >99% case with metadata included.

Raw UTF-8 text is also measured. Four-byte character IDs usually **expand** raw
text. Already-tokenized corpora do not gain another 99% saving. The dense baseline
is a counterfactual array of per-token embeddings, not an assertion that normal
training systems store their corpora in that form.

Published bytes include the manifest and token payload. Miner-local source text,
cached Merkle trees, the shared embedding definition, Python object overhead,
replication, signatures, filesystem allocation, and audit traffic are different
costs. The demo reports raw source size, cached digest bytes, and an audit response
size separately. Neither "published" nor "payload" means total network storage.

## 4. Commitments and trust model

A source manifest records a source identifier, SHA-256 of exact UTF-8 bytes,
UTF-8 length, character count, and a Merkle root over indexed UTF-8 characters.
The validator pins this source manifest from an independently accepted source
before issuing a challenge. A miner's own assertion is not independent evidence
of provenance. In the local demo both parties receive the same fixture; source
registration is explicitly timed and is linear work.

A block manifest binds that source record, tokenizer and embedding specifications,
token count, SHA-256 of the token file, and a separate Merkle root over indexed
uint32 tokens. Its canonical JSON uses sorted keys, compact separators, ASCII
escapes, and no floats. SHA-256 of domain-prefixed canonical bytes is the block
identifier. Those same bytes can be signed with an external signing system.
This prototype produces **signable bytes**, not public-key signatures or identity
authentication. A digest by itself does not prove authorship.

Leaves and internal nodes have distinct hash prefixes; source and token leaves
also have distinct domains. Leaves bind their index and value length. An odd last
node is duplicated at each level. Proof verification checks the expected tree
depth, index, leaf count, root, and duplicate-sibling rule. The manifest binds the
count because duplication alone does not uniquely commit to tree length.

Trust assumptions are collision/second-preimage resistance of SHA-256, correct
local code, an independently trusted source commitment, and unpredictable
validator sampling after a fixed commitment. The PoC is not a reviewed security
protocol. It proves sampled source/token consistency, not source truth, consent,
licensing, dataset diversity, semantic safety, persistent possession, or useful
computation. Correctly tokenized malicious source content passes this check.

## 5. H1.2: Probabilistic integrity auditing

### 5.1 Challenge and response

1. Validate the manifest against the pinned source and local tokenizer settings.
2. After commitment, the validator draws k distinct positions uniformly without
   replacement using the operating system's random source and creates a nonce.
3. The miner returns each requested raw character, claimed token ID, and separate
   source/token inclusion proofs, binding its response to the block and nonce.
4. The validator verifies both proofs and re-tokenizes the character. Missing,
   duplicate, unexpected, malformed, or mismatching answers fail the audit.

The validator retains its original challenge. It does not accept a replacement
challenge supplied by the miner. No challenge seed comes from the miner's
deterministic embedding recovery seed. Fixed random seeds are used only in tests
that explicitly inject reproducible sampling.

Returning snippets reveals their contents. The protocol therefore is an
**interactive sampled integrity audit**, not a zero-knowledge proof. Merkle proofs
authenticate answers, but do not make those answers secret or validate every
unqueried token. Probabilistic remote data checking has prior art in provable
data possession [4]; this toy protocol does not implement that paper's scheme.

### 5.2 Detection probability

For N positions with m fixed inconsistent token/source pairs and a uniform sample
of k distinct positions chosen after commitment:

```text
P(detect) = 1 - C(N-m, k) / C(N, k)
         = 1 - product(1 - m/(N-i), i=0..k-1), when k <= N-m.
```

If k>N-m and m>0, detection is certain in this idealized model. For a single
corrupted position, P(detect)=k/N. Thus sampling <1% of positions detects a single
randomly located error with <1% probability, not 100%. For corrupt fraction q
and relatively small k, P(detect) is approximately 1-(1-q)^k. At q=0.01,
459 independent draws suffice for approximately 99% detection; sampling without
replacement is at least as strong. Cryptographic failure assumptions remain.

**H1.2, revised:** An inconsistent sampled response is rejected, subject to the
hash assumptions. The block-level detection probability follows the formula;
execution cost is an empirical measurement. The original conjunction of 100%
detection and <1% computation is not established and is false in general.

### 5.3 Cost and experimental method

Source registration and miner ingestion each perform O(N) work; cached trees use
O(N) space. With caches ready, proof generation and verification cost O(k log N)
hash operations and communicate O(k log N) hashes. Sampling a small fraction of
tokens does not imply the same fraction of runtime or network bytes. A normal
full-file digest verification also requires reading the full payload; that is a
separate check available to consumers downloading the published token file.

The demo times source registration, miner ingestion, dense materialization, and
each challenge/response/verification phase separately. It reports the median of
repeated complete audits, measured relative to ingestion without embedding
generation. No time threshold is asserted in unit tests. Timing excludes network
and disk for the protocol experiment, and reports no WAN performance claims.

Tests include a block committed with all tokens incorrect (certain detection
with any nonempty sample), a sparse corruption experiment enumerating every
possible small sample, and an intentionally missed sparse error. The latter is
essential evidence against an unjustified certainty claim. Additional adversarial
tests cover forged snippets/proofs, replaced commitments, wrong tokenizer settings,
replayed responses against fresh challenges, truncation, and malformed metadata.

## 6. Stage completion and next experiments

Stage 1 is complete when the local code reproduces lookup bytes, publishes stable
artifacts, measures both successful and unsuccessful savings cases, rejects
tampered sampled answers, and agrees with the detection formula on exhaustive
small cases. Automated tests and the demo JSON record these results. They do not
establish the remaining stages.

The next useful experiment is a two-process miner/validator transport using the
same commitments, bounded requests, and explicit source provenance. After that,
replace the toy tokenizer with a pinned real tokenizer and specify audit context.
Before a large peer network, measure a tiny model across two pipeline stages with
injected latency and compare accuracy, memory, traffic, and throughput against a
single-process baseline. Signed manifests, durable storage/availability, poisoning
resistance, and incentive design need their own acceptance criteria.

## 7. Whole-project roadmap and deferred decisions

Open questions are recorded here so they can be tested when their stage begins.
They are not prerequisites for finishing the local ingestion experiment.

| Stage | Working experiment | Evidence required at that stage | Deferred mechanism |
| --- | --- | --- | --- |
| 1. Data ingestion | Independent source commitment, token manifest, deterministic spot checks | Reproducible bytes, explicit sample detection probability, storage and runtime measurements | Network storage, real tokenizer, signatures, data admission policy |
| 2. Forward computation | Tiny transformer split across two processes; attention remains local to each layer group | Agreement with an unsplit model; measured activation traffic and behavior under delay/failure | Heterogeneous GPU placement and peer discovery |
| 3. Loss and evaluation | Versioned evaluation jobs and held-out comparisons against the parent model | Reproducible scores within stated numerical tolerance; useful evaluation cost | Validator selection, private test data, collusion resistance |
| 4. Backpropagation and updates | Version-consistent backward passes; bounded-delay experiments against synchronous training | Gradient checks, learning curves, throughput, memory, and failure recovery | Large-scale asynchronous scheduling and optimization rules |
| 5. Branches, selection, and merges | Content-addressed model DAG; explicit parent/data lineage; optional merge candidates | Independent branch usability; evaluated merge quality and preserved provenance | Discovery, pruning policy, rewards, and ancestry accounting |

Two distribution strategies should remain distinguishable: splitting one training
job across layer workers, and letting separate workers train complete compatible
models/adapters locally before publishing branches. The latter reduces frequent
cross-worker dependencies but requires each worker or local group to fit the
needed model. Neither strategy is selected as universally best by Stage 1.

Later incentive experiments should start with simulated credits and controlled
participants. Questions include what earns credit, how to recognize duplicates,
how to account for copying and fake demand, and how to fund verification. A public
weight file can be used offline; automatic per-use royalties cannot be assumed
enforceable simply because lineage is recorded. Different funding mechanisms can
be compared without blocking the data protocol.

## 8. Initial related-work map

This is a starting bibliography, not an exhaustive novelty assessment. Existing
projects provide baselines and reusable work while leaving room for different
combinations, objectives, and empirical results.

| Work | Relevant overlap | Comparison to investigate later |
| --- | --- | --- |
| [Petals](https://arxiv.org/abs/2209.01188) | Collaborative model inference and efficient fine-tuning across participants | Can DAWL reuse serving/training interfaces while adding independent branch lineage and evaluation? |
| [DiLoCo](https://arxiv.org/abs/2311.08105) | Training with infrequent communication between worker groups | Compare local-update branches against a coordinated low-communication optimizer. |
| [Streaming DiLoCo](https://arxiv.org/abs/2501.18512) | Overlapping communication and training, partial synchronization, quantized transfers | Establish a communication baseline before claiming a pipeline improvement. |
| [Gensyn](https://docs.gensyn.ai/) | Distributed execution, verification, and coordination infrastructure | Identify reusable verification assumptions and what changes for independently selected model branches. |
| [Bittensor subnets](https://guides.learnbittensor.org/subnets/understanding-subnets) | Miners, validators, and task-specific incentive mechanisms | Compare reward objectives and decision authority; a miner/validator vocabulary alone is not a distinction. |

The author's [initial design discussion](https://share.google/aimode/iiV8ygv5bICUWzyo5)
provides motivation and design history. AI-generated explanations in that discussion
are not used as scientific evidence. The resulting hypotheses, assumptions, and
experiments are what this repository will develop.

## References

1. Narayanan et al. (2019), [PipeDream: Generalized Pipeline Parallelism for DNN
   Training](https://www.microsoft.com/en-us/research/publication/pipedream-generalized-pipeline-parallelism-for-dnn-training/).
2. Hu et al. (2021), [LoRA: Low-Rank Adaptation of Large Language
   Models](https://arxiv.org/abs/2106.09685).
3. Wortsman et al. (2022), [Model soups: averaging weights of multiple fine-tuned
   models improves accuracy without increasing inference time](https://arxiv.org/abs/2203.05482).
4. Ateniese et al. (2007), [Provable data possession at untrusted
   stores](https://researchwith.stevens.edu/en/publications/provable-data-possession-at-untrusted-stores/).
