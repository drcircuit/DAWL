# External cluster bootstrap and local rehearsal

The external workflow creates a **fresh four-validator chain** from the current
public corpus and provenance checkpoint. Each operator generates its own keys,
exports a signed public registration, reviews one shared network bundle and then
installs that bundle locally. The assembler never needs operator private keys.

No external hosts are available for this stage. The automated exercise uses
isolated containers on one machine to rehearse the ceremony and network failures.
It does not establish operator independence, WAN throughput or Internet readiness.
The existing `cluster-v2` chain is a preserved baseline and remains separate.

## Shared and private material

Every validator gets byte-identical `genesis.json`. It defines the same chain ID,
four equal-power validators, application plan and initial state. Three votes are
required to commit. Peer addresses are rendered into each node's `config.toml`.

| File | Distribution and purpose |
|---|---|
| `registration.json` | Public signed role, name, mesh address and public identity; validator registrations also prove possession of a separate P2P key |
| `network.json` | Public manifest pinning the image digest and every public bundle file |
| `genesis.json`, `plan.json` | Shared consensus and workload definitions |
| `trust.json`, `lineage.json`, `artifact-index.json` | Original public ingestion policy, signed lineage and allowed artifact identities |
| `key.private.pem` | Local worker/publisher/registration signing key; also the validator identity's private signing material for that role |
| `comet/config/priv_validator_key.json` | Local validator signing key, never sent to the assembler |
| `comet/config/node_key.json` | Separate local P2P identity |
| `comet/data/priv_validator_state.json` and consensus databases | Local anti-double-signing and recovery state; must be retained |

Registrations prove control of keys, not unique people or trustworthy operators.
Operators must compare the admitted identities and independently agree on the
network manifest SHA-256. A manifest and a hash fetched together from an untrusted
assembler are not an independent trust bootstrap. There is no dynamic admission,
staking, token reward or voting-power update in this version.

## Transport and operating assumptions

The generated deployment targets **Linux containers on a preconfigured private
IPv4 mesh**, such as a WireGuard network. Supported address ranges are RFC 1918 and
100.64.0.0/10. Each role instance currently needs its own mesh address; additional
addresses on the same host are possible, but do not create independent operators.
The scripts do not install a VPN, change firewall rules, acquire hosts or expose
public services.

- P2P listens on the registered address, port 26656.
- RPC listens on that address, port 26657, with unsafe RPC methods disabled.
- Providers serve port 8900 on their registered address.
- The Go adapter and Python ABCI endpoint stay on container-local loopback.
- Compose uses Linux host networking. Restrict these ports to the admitted mesh;
  HTTP RPC/artifact traffic relies on that network for transport protection.
- Keep one active process per validator or worker identity. The container must
  have write access to its own state directory. Identity files use mode 0600 on
  POSIX; operators must retain appropriate host access controls.

The tool currently requires exactly four validators, one publisher, at least one
provider and enough worker keys for the plan's replica count. It accepts 8–32 total
identities in the original rehearsal; the Azure bootstrap raises that bound to
64 identities. These are implementation bounds, not measured capacity claims.

## Build the runtime

From the repository on a Linux/WSL host with Podman:

```sh
podman build -f consensus/Dockerfile.build -t localhost/dawl-consensus-build:0.1 .
podman build -f Dockerfile.cluster -t localhost/dawl-cluster:external-v2 .
```

The Go dependencies are locked, verified and built in readonly module mode. The
Python dependency versions are pinned in the existing requirements files. Use
the **same built image** for assembly, installation and runtime: the workload plan
pins the Python source digest. Changing source requires a deliberately new plan
and chain for this prototype. It is not a rolling-upgrade protocol.

Distribute the image through an agreed registry or an OCI archive. Assembly
requires an `image@sha256:<manifest-digest>` reference. An image configuration ID
is not interchangeable with its registry manifest digest. No image has been
published to an external registry by these tools.

## Operator ceremony

The examples below assume the exact built source and pinned dependencies are
available through `python`. The same commands are included in the runtime image
at `/app/dawl_external.py` and may be run with a bind mount restricted to that
operator's directory. Do not mount all operators' key directories into an
assembly container.

**1. Each operator creates one unused identity directory.**

```sh
python dawl_external.py identity --root node-a --name validator-a \
  --role validator --host 10.42.0.10
```

Repeat on the other operators' machines with their own names, roles and addresses.
Supported roles are `validator`, `worker`, `provider` and `publisher`. Send only
`registration.json` to the assembler. The command refuses existing directories.

**2. Assemble one public bundle.**

The template must be a provider directory with the existing public plan,
artifact index, trust policy and ledger. Assembly verifies its ledger checkpoint,
retains the corpus/weight descriptors, replaces admitted worker/publisher keys,
pins the current implementation and requires a new chain ID.

```sh
python dawl_external.py assemble --root public-network --template template-provider \
  --chain-id dawl-external-001 \
  --image registry.example.org/dawl@sha256:<manifest-digest> \
  --registrations registrations/*.json
```

The output contains `network_sha256`. Agree on that value and the public membership
through an independent channel before installation. The public bundle contains
no private keys or live databases.

**3. Each operator installs the agreed bundle.**

```sh
python dawl_external.py install --root node-a --bundle public-network \
  --network-sha256 <independently-agreed-manifest-pin>
```

Installation verifies file pins, local key possession, admission, source version,
genesis/plan consistency and the original lineage checkpoint. It refuses an
already installed node, a validator that has signed, or existing consensus data.
An interrupted installation leaves an `install-started.json` marker and requires
inspection; it does not silently reset or regenerate anything.

The generated `compose.json` uses the pinned image, only the local node volume,
a readonly root filesystem, dropped capabilities and per-container CPU/memory
limits. Inspect the UID/GID if transferring a directory between operating systems.
It enables a container init process to forward termination signals and reap
verifier subprocesses; do not omit `--init` when translating to direct Podman runs.
Podman needs its init helper (such as the `catatonit` package) installed on the host.

**4. Seed providers and start each role.**

```sh
python dawl_external.py seed-provider --root provider-a --objects public-objects
cd node-a
podman compose -f compose.json up -d
```

Seeding copies only allowlisted artifacts and verifies their hashes. It is
restartable and does not copy source-producer keys. Validators and providers can
start before workers. Workers wait for a valid quorum-certified state and retry
when providers are unavailable. Container Compose support must be installed on
the operator's host; the rehearsal directly invokes Podman instead.

The publisher is an on-demand command, not a permanent scheduler. From its own
installed directory, after complete verified coverage:

```sh
podman compose -f compose.json run --rm node publish --root /node
podman compose -f compose.json run --rm node plan-training --root /node
```

These requests finalize through the same validators. Training remains disabled.

## Certified worker reads

External installations set `verify_state: true`. Each worker/publisher invokes
the Go verifier before using observed state. The verifier checks:

1. The independently pinned genesis and fixed validator set.
2. The quorum signature and header at H+1, which certifies application state H.
3. The exact state hash and a persisted minimum accepted height.
4. A maximum certificate age of 120 seconds and at most ten seconds of future
   clock skew. Operators therefore need reasonably synchronized clocks.

The client also checks its chain/plan pins and persists the accepted height/hash
in SQLite, rejecting rollback or a conflicting state at the same height. Invalid
or unavailable RPC endpoints trigger failover. There is no fallback to unsigned
state. An untrusted mempool rejection does not erase the signed transaction's
durable outbox; committed actor state or expiry resolves uncertain submissions.

This verifies authenticity and applies a bounded staleness policy. It does not
prove the response is the newest possible state, worker computation, or source
truth. During quorum loss, a new H+1 certificate may be unavailable and worker
progress appropriately pauses. The original v2 local clients retain their
previous observation mode; this does not silently alter an existing deployment.

## Evidence before punishment

New plans enable `dawl.incidents/1`. The state records up to 256 recent incident
summaries and aggregate counts. Original transaction/block history remains in
CometBFT; bounded recent summaries are not a permanent replacement for it.

- **Lease expiry:** records worker, job, generation and expiry height, explicitly
  with `misconduct_proven: false`. Work becomes reclaimable.
- **Worker-reported invalid/unavailable/error:** attributed observations with
  the receipt ID and diagnostic. A worker's accusation is not a proof of fraud.
- **Conflicting worker results:** the publisher can submit `record_worker_conflict`
  with two signed result transactions. They must concern the same worker, chain,
  plan, job and lease generation, one must be the committed passing attestation,
  and the outcomes must contradict as `pass` versus `invalid`. Repeated delivery,
  different timings, availability errors or different generations do not qualify.
  Up to 64 distinct signed pairs are retained. The affected job becomes disputed
  and blocks a new dataset release; no key is slashed or removed.
- **Validator misbehavior:** the private ABCI adapter forwards CometBFT's
  engine-validated misbehavior summaries into finalized application state.
  The full consensus evidence remains in the corresponding CometBFT block.

Recording evidence changes no voting power and transfers no money. Dispute
resolution and admission penalties need a separate explicit policy. A release
that predates later evidence remains an immutable historical record; consumers
must also inspect the current incident/dispute state. This prototype has no
automatic release-revocation mechanism.

## Rehearse without external hosts

On this workspace's WSL host, with the preserved v2 provider available:

```powershell
wsl -d kali-linux -u root --exec python3 scripts/exercise_external.py
```

The script uses a new `/var/lib/dawl/external-lab-001` tree and a separate
`10.89.91.0/24` bridge, refusing existing output/state roots. It generates all
identities in separate container mounts, assembles from public registrations,
installs the same bundle, and seeds two independent provider copies.

It disconnects validators from the network, verifies progress with three and
halt with two, restores connectivity, abandons a worker lease, injects an RPC
that alters application state, stops one provider, and injects corrupt artifact
pieces with a 100 ms delay. Workers must still finish the full corpus through
certificate verification and content-hash checks. The final dataset state is
checked by a keyless public verifier.

All rehearsal containers are stopped at the end, and their state is retained.
Existing cluster-v2 containers are not selected by the exercise. A genuine
multi-host deployment, controlled throughput study, bandwidth-loss profile and
malicious-validator double-vote test remain future work.

## Protocol references

- [CometBFT genesis and peer configuration](https://github.com/cometbft/cometbft/blob/v0.38.26/docs/core/using-cometbft.md).
- [CometBFT evidence model](https://github.com/cometbft/cometbft/blob/v0.38.26/spec/consensus/evidence.md).
- [ABCI FinalizeBlock and misbehavior](https://github.com/cometbft/cometbft/blob/v0.38.26/spec/abci/abci%2B%2B_methods.md).
