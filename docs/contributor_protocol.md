# Incremental contributions and role discovery

The Collective separates three questions: where a peer can be reached, what it
offers to do, and which claims the ledger accepts. Discovery does not establish
identity uniqueness, competence or permission to vote.

## Current protocol

1. A newcomer obtains the public bootstrap bundle and checks its network hash.
   It generates its own signing key. No existing private keys or genesis edits
   are involved.
2. Each peer advertises a signed, expiring record with its public key, endpoint
   and roles. The pilot implements Kademlia-style XOR routing with 160-bit IDs,
   eight contacts per bucket and three parallel queries. Records live for at most
   180 seconds and are refreshed and replicated near their role's key.
3. The contributor signs a provider announcement on the existing chain. In this
   pilot it offers `PROVIDER` and `TOKENIZER`. The DHT can advertise other roles,
   but the existing auditor and Oracle permission lists remain in effect.
4. The contributor discovers a Cortex through the DHT, downloads the pinned
   tokenizer and embedding matrix, obtains the selected original texts, then
   signs its source, token and vector lineage. Its public artifact endpoint
   serves only content-addressed objects in the published index.
5. A contribution transaction commits to the signed manifest, catalog, complete
   replay boundaries, shared transform hashes and training-input manifest.
   The Oracle enforces structural bounds, duplicate-book exclusion and full,
   non-overlapping coverage. It does not download or execute the corpus.
6. Existing Daemons discover the contributor through the DHT, match its key and
   endpoint to the committed announcement, fetch the artifacts, verify lineage,
   rebuild the catalog and replay their leased source/token/vector boundaries.
   Two distinct admitted auditor keys must attest to every shard.
7. Only complete audit coverage permits an incremental Blueprint. Version 2
   records its parent release and the union of prior and new input manifests.
   The previous Blueprint remains immutable and independently addressable.

The embedding commitment is a hash of deterministic float32 input-table lookup
bytes: for token IDs `t`, `V = E[t]`. These are input embeddings, without contextual
layers or training. A hash binds a claimed result; independent replay checks it.
Signatures authenticate claims. Matching receipts and BFT consensus do not prove
that an auditor actually ran the computation or that the source is true.

## Upgrade and continuity

The initial genesis, actor sequences and historical transitions remain unchanged.
A publisher-signed runtime manifest binds the new executable and role images to
the existing network. Each validator closes and archives its signing state before
the old cloud group is stopped. Restoration requires the exact exported archive
hash and the original persistent signer fence. No running validator is cloned.

After the same chain resumes, an explicit publisher transaction activates the new
contribution rules. The original implementation hash continues to describe the
original jobs; new batches pin the activated implementation. This coordinated
pilot upgrade is not decentralized software-upgrade governance.

## Roles and rewards deferred

Certificate issuance, contribution-based promotion, auditor admission changes,
validator-set changes, staking, slashing and rewards are outside this stage.
Possible future policies include a recognized certificate issuer or a network
decision based on a peer's contribution history. Neither is selected yet.

Accepted signed audit receipts give a possible future accounting input. A points
system would still need rules for unique work, duplicate receipts, collusion,
Sybil identities, reversals and disputes. An accepted audit is a recorded claim,
not proof of independent effort, and no current counter is a reward entitlement.

## Pilot boundaries

The routing and lookup are peer-to-peer, but transport is still an invited SSH
mesh between two host failure domains. New routes need transport configuration.
This does not yet implement public Internet discovery, NAT traversal, arbitrary
peer addresses, transport admission or permissionless Byzantine resistance.
Records are bounded; signatures alone do not prevent Sybil or eclipse attacks.

The new contribution has one provider, so it is not yet resilient to that
provider's disappearance. DHT replication preserves a discovery record until its
expiry; it does not replicate the underlying book/token artifacts. Dynamic
artifact replication and availability policy are separate next steps.

The Console is read-only. Certified ledger state is distinguished from unsigned
resource telemetry. The newcomer mounts the public bootstrap read-only and its
own data and telemetry directories; it cannot write operator gates or access
existing node private volumes.

## Reproduction

Use `scripts/azure/upgrade.py publish-images`, then
`scripts/upgrade_operator.py prepare` in WSL. Close and export the existing nodes
with `scripts/azure/node_lifecycle.py`. Changes to ACI CPU/memory allocations
require recreating the container group, while retaining the private archive
shares and verifying each closed archive before restoration. `upgrade.py deploy`
requires a recorded recreation fence and refuses an existing group. The
`recreate_contribution_pool.py` recovery helper is deliberately limited to the
recorded stalled group; it is not a general deletion command.

After provisioning, use `upgrade_operator.py resume`. Activate the protocol,
launch the contributor, and
publish after complete coverage. These scripts are scoped to the recorded pilot;
they are not a general public deployment installer.

`upgrade.py deploy --ssh-only` isolates transport provisioning with Console
ingress disabled. It is a diagnostic stage, not a completed public Console
deployment. A pending ARM operation, running container wrappers, or a DNS record
alone does not establish that the mesh is reachable. Verify authenticated SSH,
certified chain continuity, then public HTTPS before reporting success.

`scripts/inspect_release.py` verifies the certified Blueprint and streams every
released input manifest without a signing key or model training. The older
`dawl_cluster.py inspect-loader` command inspects only the original bootstrap
plan. Use the release inspector for an incremental Blueprint.

Tests cover malformed or unauthorized joins, unchanged baseline state, duplicate
books, missing coverage, altered transforms, distinct audit requirements,
append-only releases, signed role lifetimes, replicated lookup after bootstrap
loss, catalog reconstruction, and restoration with exact closed-state hashes.
