# Ingestion-node pilot: observed results

Recorded on 2026-10-13. This run exercised separate ingestion roles without model
training, tokenizer fitting, embeddings, gradients, or GPU work. The
[machine-readable record](ingestion_nodes_results.json) contains measurements,
artifact hashes, container configuration, and two signed validator receipts.

## Executed topology

Windows local CPU registered the source and tokenized it with an existing pinned
GPT-2 tokenizer. Podman 5.8.6 in the existing Kali WSL distribution ran two distinct
Linux containers: storage and validator. An independent Windows consumer requested
audits through the validator API and downloaded all token shards from storage.
The validator reached storage across a container bridge and had its own signing
key and tokenizer artifact. It did not have the miner private key or source store
mounted into its filesystem.

Both application containers ran as UID/GID 1000, with read-only root filesystems,
2-CPU quotas, 768 MiB memory limits, 128-process limits, and no GPU devices. Only
the validator's receipt directory was writable among the bind mounts. Published
ports were bound to 127.0.0.1:8765 and 127.0.0.1:8766. The runtime itself ran as WSL
root. This demonstrates process, filesystem, identity, and HTTP role boundaries
on one physical computer; it does not demonstrate independent organizations or a
wide-area network.

## Data and measurements

The fixture was Project Gutenberg's *Alice's Adventures in Wonderland*, including
its downloaded source notice. The tokenizer was GPT-2 revision
`210ab4c33a01af45a1914b1cf1928eb915565697`, loaded by `tokenizers==0.23.3`.

| Measurement | Observed value |
| --- | ---: |
| Raw source UTF-8 | 174,311 bytes |
| Committed chunks | 43 |
| Token IDs | 52,991 |
| Persistent token shards | 4 |
| uint32 token payload | 211,964 bytes |
| Snapshot, including tokenizer and metadata | 1,585,896 bytes |
| Tokenizer artifact alone | 1,355,256 bytes |
| Initial ingestion wall time | 0.4233 s |
| Eight-chunk audit: validator verification | 0.0537 s |
| Eight-chunk audit: manifest fetch, challenge, verification | 0.7718 s |
| Eight-chunk response | 84,896 bytes |
| All-43-chunk audit: validator verification | 0.1473 s |
| All-43-chunk audit: manifest fetch, challenge, verification | 0.7038 s |
| All-43-chunk response | 455,253 bytes |
| Windows consumer download and verification | 0.5876 s |

The snapshot byte count excludes retained raw source objects, private identities,
receipts, API traffic, container images, and local consumer replicas. The raw
source is retained in addition to the snapshot. Token payload alone is about
1.216 times the source size: **this run does not compress the original text**.
Keeping a tokenizer copy in each snapshot also dominates this small fixture;
shared content-addressed tokenizer storage is a later optimization.

Timings are single observations, not a benchmark or scaling claim. The full audit
ran after the sampled audit with warm caches and finished its combined request
faster despite doing more verification. Neither timing establishes a universal
ratio between validation cost and ingestion cost.

## Acceptance evidence

- All 59 tests passed: 32 original protocol tests, 22 ingestion/persistence and
  adversarial tests, and 5 tests using separate HTTP server processes.
- A correctly signed snapshot with matching file hashes but intentionally wrong
  tokens passed integrity verification and failed independent re-tokenization.
- Tampered signatures, corrupt token files, altered source bytes, bad proofs,
  replayed responses, missing/duplicate evidence, and unauthorized API requests
  were rejected by their corresponding tests.
- The container validator accepted both eight sampled chunks and all 43 chunks;
  Windows independently verified both receipt signatures against its pinned key.
- A fresh Windows consumer copied and verified all 43 chunks and 52,991 tokens.
- Storage and validator containers were removed and recreated on the final image;
  the persisted snapshot passed both audits and consumer replication again.
- A newly published Windows snapshot was verified inside a non-root Linux
  container with networking disabled, confirming readable portable publication.

Eight samples give only `8/43 = 18.6047%` detection probability for one bad chunk
fixed before the challenge. The 43-chunk audit covers the complete fixture. These
claims concern deterministic source/token consistency, not the source's quality,
authorship, rights, usefulness, or continuous availability.

The container exercise exposed two integration issues that local unit tests did
not cover: an internal-only Podman network that did not support the required
localhost forwarding, and Windows temporary-directory ACLs unreadable through
WSL by an ordinary container UID. The deployed bridge and publication staging
were corrected and rechecked. The final image ID is recorded in the JSON.

## Remaining boundary

This is a working local deployment pilot, not an open public network. Docker
Compose configuration is supplied but was not executed; Podman was the tested
runtime. Multi-host networking, load and recovery testing, durable queues, quotas,
source admission policy, public identity admission, incentives, and training are
not validated by this run. The next ingestion experiment can distribute existing
hats across another host and introduce job scheduling without adding training.

See the [architecture and run guide](ingestion_nodes.md) for contracts and commands.
