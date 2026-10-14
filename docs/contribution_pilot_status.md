# Contribution pilot: network recovery and newcomer trial

Status at 2026-10-13 23:25 UTC: networking and the hybrid chain are restored.
The five-book newcomer completed ingestion and independent audits; the 13-book
Blueprint is published and its inputs passed a keyless streaming check. This is
a successful bounded join experiment, not a general scaling result.

## Current verified state

- The real nine-container Azure group is `Succeeded` and `Running` in Norway East.
  All seven node archives were restored with their original identities.
- The chain resumed at height 1595 with exactly the original chain ID, plan and
  eight-book Blueprint. All four validators are reachable. Both Consoles return
  fresh quorum-certified state without observer errors.
- Public SSH 2222 is authenticated with an Azure-management-pinned host key.
  [The cloud Console](https://dawlh1013185918-collective.norwayeast.azurecontainer.io/)
  returns HTTP 200 with normal HTTPS certificate validation. The local Console
  remains at `http://127.0.0.1:8882/`.
- Requests remain 3 CPU / 8 GB. Seven compute containers retain 3-CPU burst
  limits; gateway and Console limits are now .3 and .2 CPU respectively. This
  configuration passed two isolated ingress controls and the real deployment.
- The original stop workflow is enabled, with its restored permission verified
  at the exact ACI group scope. The original 24-hour total window is unchanged.
- Protocol activation committed at height 1616. The isolated newcomer launched
  at 23:16:57 UTC with its own key, a public bootstrap and two DHT seeds. All 27
  original peers returned its correctly signed, unexpired provider record during
  the final discovery check. Both Consoles show 28 running nodes.
- Eight new jobs have two passing receipts each from distinct auditor keys:
  sixteen auditors in total, thirteen local and three in Azure. These jobs have
  no failed receipts or remaining leases. The entire corpus has 34 completed jobs.
- Blueprint `fd8760e4f3878a1bcb79645f42f0f55bcbbaab74f20d9aa2cfe53ac005743950`
  published at height 1786. It explicitly names original Blueprint
  `8f16cf340684a69ed67b5614b5bda972a76af4dc205e9dcd9863910f561280c6`
  as its parent; both releases remain in the ledger.

## Measured newcomer result

| Measure | Result |
| --- | --- |
| New Gutenberg book IDs | 35, 36, 62, 201, 5230 |
| New Frags / tokens | 369 / 404,167 |
| Released corpus | 13 books, 1,807 Frags, 2,061,330 tokens |
| New audit receipts | 16 passes covering 738 Frag replays |
| Dense vector bytes reconstructed across those replays | 2,483,202,048 |
| Local audit wall time, min / median / max | 18.862 / 25.441 / 26.175 seconds |
| Azure audit wall time, min / median / max | 112.545 / 112.827 / 113.650 seconds |
| Keyless input check | Two manifests, 13 books, 1,807 Frags, 2,059,523 loss targets |

Audit wall time includes fetching the contribution, rebuilding its catalog and
replay; it is not an isolated CPU benchmark. Local and cloud cache conditions
differed after recovery. The five raw books were prefetched before joining, so
this trial is not a cold end-to-end download benchmark. Historical worker and
DHT retry counters must not be interpreted as failures of the new audit jobs.

The input check verified manifest coverage, chunk uniqueness and book-level
split separation. `source_tokens - chunks = target_tokens` holds across the
released sequences. No signing keys or model training were used by this check.
Signed receipts are replay claims accepted under the current admitted-auditor
policy; they are not cryptographic proofs that computation occurred. The transport
still spans two host failure domains through invited SSH routes; public NAT
traversal, unrestricted enrollment and automatic artifact replication remain
outside this experiment.

Public evidence is retained in
`artifacts/azure-hybrid-001/contribution-public-evidence/`, including the verified
certificate/state, signed discovery records, input-check result and run summary.
Network recovery checks are in `network-recovery-*.json` beside that directory.
The separate private archive directories must never be included in shared reports.

## Earlier authentication and deployment failures

Azure CLI returned a valid management token and successfully read resource group
`DAWL`. Signing in again is not currently required. A previous token-renewal
attempt encountered a DNS lookup failure; later authenticated management calls
succeeded. A separate Windows CLI console-handle error occurred when submitting
a deployment. The deployment script now uses a fresh deployment name and an
asynchronous submission with bounded status checks.

The nine-container ACI group in Norway East started its containers, but stayed in
`Creating` with no public IP in the management response. DNS resolves; external
SSH and HTTPS connections failed to return application responses. SSH responded
inside the group on both loopback and its Ethernet interface. Image pulls and
Azure Files mounts succeeded. The controlled comparisons below led to the
resource-limit adjustment; the exact Azure internal mechanism remains unknown.

Recreating the group, changing its DNS label and testing with only SSH exposed
did not restore public connectivity. The SSH-only diagnostic deployment
disabled public Console ports. No further blind recreations should be attempted
without a new diagnostic hypothesis. The last deployment's name is recorded in
the ignored `artifacts/azure-hybrid-001/active-contribution-deployment.json`.

## State and budget preserved

- The original chain, genesis, validator identities and eight-book Blueprint
  are retained. Old and new runtimes produced the same application-state hash
  from a closed copy at height 1594.
- Each cloud node closed its state before group removal. Seven archives were
  exported, hashed and read-verified for each recreation. Private archives and
  their persistent Azure Files shares are retained; never publish these files.
- Local nodes remained stopped on their existing private volumes throughout
  diagnostics. They have since resumed after verified cloud archive restoration.
- The requested allocation remains 3 vCPUs and 8 GB. The authorized window is
  24 hours total from 2026-10-13 19:01:52 UTC, with a EUR 50 ceiling.
- Container shutdown is set for 2026-10-14 18:41:52 UTC; the Azure stop workflow
  becomes eligible at 18:51:52 UTC. It runs every five minutes. The workflow's
  stop permission was lost when its target group was deleted and was restored
  independently with `deploy/azure/node-stop-guard.bicep`; the assignment was
  verified. Timed shutdown is an operational spending safeguard, not a hard
  Azure billing cap.

## Implementation and preparation checks

The contribution protocol, signed DHT role discovery, independent replay path
and append-only Blueprint support are implemented. The full suite previously
passed 157 tests; three additional archive/upgrade tests passed, and all 16
hybrid checks passed again during recovery. Those unit checks are supplemented
by the live join, audit, publication and keyless input results above.

The five Gutenberg originals (35, 36, 5230, 62 and 201) were fetched and verified
into the isolated contributor directory: 1,504,669 raw bytes. This fetch happened
before network joining and must be excluded from any future cold-join timing.
The contributor subsequently completed ingestion and the 13-book Blueprint was
published. No model training has taken place.

## Completed recovery and experiment checks

1. Diagnose the ACI public-connectivity failure and verify reachable SSH. Obtain
   the SSH host key through authenticated Azure management, never network TOFU.
2. Verify public Console ingress and normal certificate validation; the
   earlier SSH-only deployment was only a diagnostic stage.
3. Resume the same chain using the signed runtime manifest and exact closed
   archives. Verify its original Blueprint and certified state before activation.
4. Activate contributions, launch the newcomer, observe DHT discovery across
   both hosts, independently replay all new shards, and publish the incremental
   Blueprint only after complete coverage.
5. Run the keyless release inspector, verify both Consoles and capture measured
   results. Auditor/Oracle admission and rewards remain deferred.

## Requested stop and restart

At the user's request, the cloud group was fully stopped and confirmed `Stopped`
at approximately 21:40 UTC on October 13. All seven node processes first closed
cleanly. Their new archives were downloaded, hashed and read-verified; the
restart configuration was updated to pin these exact new archives. Archive
checksums can change on graceful closure, so reusing the previous restore pins
would fail the intended integrity guard.

The same group was restarted with unchanged images, resource allocation and
deadline. All nine containers have fresh start times around 21:42:28 UTC. The
seven nodes passed archive restoration and identity checks, then waited for
public bootstrap. External SSH still timed out after restart and the group
remained `Creating` without an API-reported public IP. Thus the container restart
succeeded, but did not recover the testnet's public connectivity. Detailed
evidence is retained in ignored `artifacts/azure-hybrid-001/restart-*.json` files.

The gateway host key was refreshed through authenticated Azure management.
No validator reset, new genesis or contribution activation was performed.

Resource allocation changes require ACI recreation; see Microsoft's
[update limitations](https://learn.microsoft.com/en-us/azure/container-instances/container-instances-update#limitations).
Microsoft also documents the intended
[Caddy sidecar pattern](https://learn.microsoft.com/en-us/azure/container-instances/container-instances-container-group-automatic-ssl).
These documents describe supported configurations; they do not establish the
cause of this deployment's failure.

## Isolated network review

The main cloud group was stopped while diagnostic groups ran sequentially within
the same 3-vCPU ceiling. Local signers remained stopped. Seven closed cloud archives
were compared with the original checkpoint: every regular file has identical
content. No chain state was reset or advanced during these tests.

The actual public ACI declaration exposes TCP 2222 at both group and gateway
container levels. There is no attached VNet, subnet, network profile, NSG, route
table or customer-managed network gateway/firewall in this deployment. The
gateway's SSH server listens on `0.0.0.0:2222`; internal SSH was verified. Public
80/443 were deliberately disabled in the SSH-only diagnostic configuration and
are enabled in the recovered deployment.
The Console normally uses Caddy on 80/443 and an observer on `127.0.0.1:8882`.
Its current entrypoint waits for public bootstrap files before starting either
listener; that dependency is separate from the reproduced SSH-only failure.

Disposable probes use the pinned gateway image with a simple HTTP server on
2222 and sleeping containers. They contain no node keys, ledger state or storage
mounts. Thus they separate ACI provisioning from DAWL startup and replay work.

| Configuration | Observed result |
| --- | --- |
| One container, 1 CPU / 1 GB | Succeeded; external HTTP payload verified. |
| Nine containers, current 3 CPU / 8 GB allocations | All running; Creating with no API-reported IP after the bounded observation window. |
| Nine containers, gateway .5 CPU / .5 GB and adjusted provider/Console allocations | Same failure within the observation window. |
| Nine containers, gateway placed last | Same failure within the observation window. |
| Eight containers, original allocations and omitted private port properties | Succeeded; external HTTP payload verified after 36 seconds. |
| Nine containers, omitted empty private port properties | Did not recover; Azure eventually reported a 30-minute provisioning timeout. Diagnostic processes exited successfully at their ten-minute deadline. |
| Nine containers, no explicit resource limits | Succeeded; external HTTP payload verified after 98.9 seconds. |
| Nine containers, compute limits 3 CPU, gateway limit .3 CPU, Console limit .2 CPU | Succeeded; external HTTP payload verified after 37.3 seconds. |
| Same auxiliary limits, plus public port declarations 80/443 | Succeeded; external HTTP payload verified on 2222 after 41 seconds. The sleeping Console deliberately had no listeners. |

The working controls demonstrate public access on TCP 2222 from the operator
machine. The failing nine-container probes do not establish a general ACI
container-count limit or a confirmed root cause. Container ordering and the
tested alternative allocation did not recover ingress. Explicit resource limits
are implicated by the successful comparisons; the exact Azure internal failure
mechanism is not established. The deployment template now retains 3-CPU burst
limits for the seven compute nodes and caps gateway/Console CPU at their .3/.2
requests. Total requests remain 3 CPU / 8 GB. Further controlled
comparisons are recorded under `artifacts/azure-hybrid-001/network-probe-*`.
Each temporary probe is deleted after measurement; its scripts also impose a
ten-minute process lifetime. Management calls in the diagnostic helper now have
a 60-second timeout; REST reads avoid waiting for provisioning completion.

Microsoft documents [shared group networking and port declarations](https://learn.microsoft.com/en-us/azure/container-instances/container-instances-container-groups)
and a [60-container group limit](https://learn.microsoft.com/en-us/azure/container-instances/container-instances-resource-and-quota-limits).
The images' lack of Dockerfile `EXPOSE` metadata does not publish or block ports;
see [Docker's EXPOSE reference](https://docs.docker.com/reference/dockerfile/#expose).
