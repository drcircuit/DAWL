# DAWL admitted swarm prototype

The swarm wraps the existing source → token snapshot → input-embedding commitment pipeline. Independent processes discover admitted peers, pull committed artifacts, replicate signed lineage, replay samples on CPU and gossip signed observations. The existing host GPU pipeline supplies vector commitments; this increment does not distribute training or schedule new GPU production jobs.

## Protocol

- `PROVIDER=1`, `AUDITOR=2`, `LEDGER_REPLICATOR=4`. Admission certificates bind the node public key, role mask and exact endpoint. Nodes independently pin membership and the original lineage trust digest. Admission never grants dataset signing rights.
- Joining nodes POST bounded, signed announcements to configured bootnodes. Peers exchange fresh announcements and connect only to endpoints in the pinned policy. Announcements expire after 120 seconds; persisted sequence high-water marks reject rollback and equivocation. Repeated gossip cannot refresh liveness. There is no DHT, open registration, staking or reward mechanism.
- Lineage replication verifies author chains, parents, signatures, roles and the advertised content-set checkpoint. Receipt gossip uses durable per-peer cursors and a persistent stream identifier; a changed stream triggers a fresh export. These are content sets, without global ordering or consensus finality.
- Artifacts travel in 1 MiB pieces, with four concurrent requests, bounded responses and timeouts, no redirects/environment proxies, and alternate providers. Persisted content-addressed pieces support deduplication and resume. Piece catalogues are untrusted: the assembled object must match the **whole-object hash already committed in the lineage ledger** before publication or execution. A dishonest catalogue can waste transfer attempts but cannot change the accepted artifact.
- The shared 154 MB embedding table is transferred once per cold cache. Dense sequence vectors are never transferred. Current sampling still fetches the complete book and token snapshot, then checks their integrity. This is file-level transport, not yet minimal per-sample Merkle evidence retrieval.
- Auditors retain a cryptographically random, uniform sample without replacement after observing a vector commitment. Challenges and retry state survive restart. The verifier checks original source, registry, tokenizer and signed snapshot, then retokenizes and reconstructs only the selected chunks on CPU. For a fixed set of `m` bad chunks among `N`, `k` samples detect corruption with probability `1-C(N-m,k)/C(N,k)`. A sampled pass is not a full audit.
- The worker executes one CPU replay at a time while discovery and gossip proceed separately. Its queue, observations and cache are durable; computation does not depend on provider authority.
- Receipts distinguish `pass`, `invalid`, `unavailable` and `error`. Failure to obtain hash-valid data is an availability failure, even when peers corrupt it in transit. A retrieved but incorrectly computed signed vector commitment is invalid. Local runtime errors remain separate.
- Each signed receipt binds the vector entry, checkpoint, nonce, exact positions, total chunks, outcome, timings and optional earlier claim being rechecked. Chunk identities resolve through the target's committed registry. Timings and approvals are attributed claims, not proofs of work. Consumers check signatures and admission; auditors independently replay other auditors' original claims at the same positions. A contradictory local replay is displayed as a dispute. Correctly guessing a pass on valid data remains undetectable as freeloading.
- Replicators batch receipt IDs into their own signed observation chains. These are local epochs, without global agreement. Swarm receipts do **not** enter the original platform audit ledger or qualify a dataset for assembly.

## Run the lab

Install `requirements-swarm.txt` into the Python environment. Five Podman containers in the `kali-linux` WSL distribution receive separate identities, ledgers and caches, two CPU cores and a 2 GiB limit each. Ports bind to host loopback. The bridge uses `10.89.88.10`–`.14`; change configuration if the subnet is already used elsewhere.

```powershell
.\.venv\Scripts\python.exe dawl_swarm.py prepare-lab --root artifacts/swarm-lab-001
wsl -d kali-linux -u root --exec sh /mnt/c/Users/espen/OneDrive/Dokumenter/ChatGPT/DAWL/scripts/swarm_containers.sh build
wsl -d kali-linux -u root --exec sh /mnt/c/Users/espen/OneDrive/Dokumenter/ChatGPT/DAWL/scripts/swarm_containers.sh up
.\.venv\Scripts\python.exe scripts/exercise_swarm.py --root artifacts/swarm-lab-001 --output artifacts/swarm-run-new
```

Preparation refuses to overwrite an existing root. For a new cold run, stop the labelled lab with `swarm_containers.sh down`, prepare a fresh directory, and supply its absolute WSL path using `env DAWL_SWARM_ROOT=...` before `sh .../swarm_containers.sh up`. Earlier development roots are retained, not presented as cold measurements.

Both providers initially withhold artifacts. The exercise partitions the replicator, makes provider A corrupt delivery and provider B delay/drop requests, stops B during a partial embedding-table transfer, restarts B, and checks recovery. It requires eight valid book audits and a local replay contradicting the freeloader's approval of a deliberately invalid ninth vector entry. Finally it heals the partition and compares lineage and receipt sets across all nodes.

The canary is a signed copy of a vector entry with deliberately wrong vector hashes. It exists only in copied lab ledgers. Preparation uses the host vector key to sign this explicit fixture; containers receive none of the original source/tokenizer/vector private keys. The original 36-record platform ledger and dataset remain unchanged.

Fault controls are immutable files under each node's `faults/` directory, enabled only by local `lab_faults` configuration. There is no HTTP fault-control route. The `freeloader` container intentionally signs dummy passes; it is an adversarial fixture. Request loss and partitions here are application-level surrogates, not a complete TCP/network emulator.

## Observe and independently verify

Copy the lab's `lab.json` to `artifacts/platform-v1/swarm-observer.json`. The existing observatory at `http://127.0.0.1:8880/` shows connections, roles, checkpoints, artifact availability, transfer counters, receipts and local replay disputes. Restart the platform service if it predates the swarm API. Inventories and topology are node reports, not remote storage proofs. The observer fetches only configured loopback origins. Swarm ports are 8900–8904; the original 8890/8891 services are separate.

Public API: `/swarm/hello`, `/swarm/peers`, `/swarm/status`, `/swarm/observations?after=0`, `/swarm/observations/{receipt_id}`, `/swarm/catalog/{artifact_sha256}`, `/swarm/pieces/{artifact_sha256}/{index}`. Replicators also serve `/api/checkpoint` and `/api/ledger`.

Anyone with independently obtained public policy pins can replay a receipt against a reachable provider/replicator, without an admitted private key:

```powershell
.\.venv\Scripts\python.exe dawl_swarm.py verify-receipt `
  --root artifacts/public-swarm-replay-new --peer http://127.0.0.1:8900 `
  --policy artifacts/swarm-lab-001/policy.json --policy-sha256 <independent-swarm-policy-pin> `
  --trust artifacts/swarm-lab-001/provider-a/trust.json --trust-sha256 <independent-lineage-policy-pin> `
  --receipt <signed-receipt-id>
```

The report says whether replay agrees with the claim. A false approval of the canary produces `outcome=invalid`, `matches_claim=false` and exit status 1: successful detection of a false claim. Downloading policy and keys from the same untrusted peer does not independently establish authority.

## Limits and next measurements

This is a single-host experiment with independent CPU containers and two configured providers. It tests recovery when an honest alternative becomes reachable. A fully eclipsed node cannot recover information unavailable to it. Provider stores are independent copies but share a physical machine and disk. Membership and lineage policy are pinned rather than dynamically governed.

Bounds: 64 admitted members, 100,000 observations, 256 MiB per artifact, four concurrent requests, up to 256 samples, a 512-artifact inventory and 100 recent receipts per status response. Full observations export is paginated. Per-operator quotas, pruning/archival, admission rotation, Internet-grade ingress protection, cancellation of long CPU jobs, DHT discovery and a deployment across machines remain future work. Artifact downloads are serialized per auditor; full-table and snapshot integrity checks cost CPU and disk I/O even when sampling is small.

Next tests should vary node count, corpus size, latency, unavailable fraction and concurrent cold auditors. Measure verified useful bytes/second, recovery time, storage/write amplification, CPU/RAM and disagreements. The chaos run's duration includes outages and retry waits, so it is not a clean throughput benchmark.

Implementation references: [Python 3.12 asynchronous tasks](https://docs.python.org/3.12/library/asyncio-task.html), [HTTPX asynchronous clients](https://www.python-httpx.org/async/), [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/).
