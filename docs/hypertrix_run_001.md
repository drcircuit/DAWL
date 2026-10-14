# Hypertrix hybrid run 001 — 2026-10-13

Historical first per-node run. Subsequent same-chain recovery, corpus growth and
GPU results are in [the consolidated update](hybrid_research_update.md).
The initial four-hour deadline recorded below was later extended, with approval,
to 24 hours total and the same EUR 50 ceiling: node shutdown at 18:41:52 UTC
on 14 October (20:41:52 Oslo), group-stop backstop eligible from 18:51:52 UTC.

The first Hypertrix network completed the entire current corpus with **one
container per admitted node**, across Azure Norway East and the local WSL host.
This report records an executed pilot, not a proposed deployment or throughput
projection. The [runbook](hybrid_testnet.md) describes the architecture and
[terminology](hypertrix_terminology.md) defines the platform names.

## Deployment and fresh identity

- 27 admitted nodes: four Oracle validators, two Cortex Units, twenty Daemons
  and one Blueprint publisher.
- Azure: seven node containers plus a gateway, with ARM requests totalling
  **3 vCPUs and 8 GB**. Local: twenty node containers plus the keyless Console.
- Six role-specific image targets; private state and archive mounts separated
  per node. The gateway mounts only the public exchange volume.
- All 27 signing identities are distinct from the completed DAWL baseline.
  Fresh registrations, genesis, leases, audit receipts and dataset release were
  generated. Existing source bytes and historical ingestion lineage were reused.
- Chain: `hypertrix-dawlh1013185918`.
- Genesis SHA-256: `8443e106c542c1e920e51862b9763300bc2079871f9d03f1cc3c46ded5de1871`.
- Network SHA-256: `c2a69886ef30d988418d8dd9d130cdec15b8b5141c3ad9b14e02bd1dd6b1c888`.
- Implementation SHA-256: `0489eeea9f95ac3dc3b25e8ed16b0a4be8001affa61736db9c6d9bbb4fa67b86`.

The original pooled cloud group was stopped before its replacement was created.
An initial Azure request was rejected because 0.75/1.25-GB memory requests were
not in 0.1-GB increments. No container group existed at that point. The corrected
allocation retained 3 vCPU/8 GB and the original pilot deadline.

## Full-corpus results

| Measurement | Observed result |
|---|---:|
| Books | 8 |
| Frags | 1,438 |
| Tokens | 1,657,163 |
| Complete jobs | 26 / 26 |
| Accepted audit Cipher-Logs | 52 |
| Attested Frag replays | 2,876 |
| Distinct contributing Daemons | 20 / 20 |
| Accepted audits from Azure / local | 16 / 36 |
| Attempts | 59 |
| Temporary unavailable outcomes | 7 |
| Expired leases / disputes / rejected transactions | 0 / 0 / 0 |
| Worker release to final evidence capture | 252.34 seconds |
| Initial public-artifact seed transfer | 717,987,840 bytes in 121.35 seconds |

The initial seed transfer precedes the reported worker interval. The interval
includes artifact fetching, replay, coordination, the individual-node fault tests
and observation cadence. It is not a pure CPU benchmark. The seven unavailable
attempts recovered through subsequent work; they must remain visible in evidence.

The Blueprint was committed as
`8f16cf340684a69ed67b5614b5bda972a76af4dc205e9dcd9863910f561280c6`.
The independent Go verifier checked its public state certificate against the
pinned genesis. Model training was not performed.

## Fault and visibility checks

| Check | Observed result |
|---|---|
| Local container rehearsal | Four validators formed a certified chain; a 2/2 partition halted and recovered |
| One Oracle process paused | `oracle-0` paused at observed height 114; the other three advanced to 118 before it resumed |
| Individual Daemon restart | `daemon-4` stopped and restarted in its own container, retaining exactly the same registration and private state |
| Azure/local WAN partition | Both halves halted at height 267 during a 15-second stable observation, after a 12-second settling period; reconnection advanced to 268 |
| Replica convergence after recovery | All four validators returned the same block hash at height 296 |
| Private validator interfaces | TCP connections to ABCI ports 26658/26659 were refused from another container at each site; adapters use private Unix sockets |
| Console | HTTP/API checks passed; 27 running nodes, four reachable validators, verified state, no observer errors |
| Console failure indication | A stale observation loses its current-certified status; JavaScript syntax checked |
| Automated checks | Full suite: 150 passed. Focused suite after the Azure allocation correction: 16 passed |

The Console is read-only at `http://127.0.0.1:8882/`. Telemetry is an observation,
not a cryptographic proof of resource use or execution. A quorum certificate is
separately verified. The node restart is a same-state restart, not restoration
after lost storage, nor a demonstrated validator recovery procedure.

## Evidence and limits

Public evidence is under `artifacts/azure-hybrid-001/node-evidence/`, including
the bootstrap bundle, role-image digests, certified release state, recovery
certificate, audit analysis, per-container mount/resource checks, fresh-identity
comparison and fault observations. The pooled baseline remains under
`artifacts/azure-hybrid-001/evidence/`. Private keys and state archives are excluded
from those public exports. Evidence directories are intentionally Git-ignored.

This demonstrates a functioning admitted network with real WAN transfer,
separate node containers, deterministic reconstruction and verifiable
ledger lineage. It does **not** demonstrate independent operators, geographic
consensus resilience, permissionless admission, reward/slashing economics,
malicious-auditor resistance or a scale-throughput law. Both sites are controlled
by one operator, and each site's validators share a host failure domain.

The next scale study should vary corpus size and concurrent work, measure
provider backpressure and queueing, and separate validator failure domains.
Repeated runs with fixed resource budgets are needed before comparing throughput.
No claim about user trust in trained-model outputs follows from this pilot alone.

The pilot remains bounded by its original node shutdown at **22:41:52 UTC on
October 13** (00:41:52 Oslo on October 14), with the Azure group-stop backstop
from 22:51:52 UTC. Registry and archive storage persist after compute stops.
