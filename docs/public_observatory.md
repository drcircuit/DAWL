# Public observatory and the network split

Public URL: https://dawlh1013185918state.z1.web.core.windows.net/

The Azure account is in Norway East, resource group DAWL. Its `$web` container
serves only the explicit output of `build_public_site.py`. Static website hosting
has no web-server compute instance. Azure still meters storage, requests and
transfer; retained registry and private node archives are separate existing
resources. General anonymous blob access remains disabled. Static website access
is independently public, as described in Microsoft's
[static hosting documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website).

On 14 October the older cloud node group was gracefully stopped. Seven closed
node archives were exported and read-verified in the ignored local artifacts
directory. Keys and archives remain private. The old pooled group also stays
stopped. The newer local protocol-4 training chain continues on its own four
validators; it has a different genesis from the older hybrid chain. This is not
an automatic merge or continuation of the partitioned old chain.

## Accepted-state publication

The local publisher fetches a certificate from the loopback observer, serializes
the exact canonical state, then runs the independent Go `check-certificate` tool.
The fixed trusted genesis pin is
`5764519561f755af339ee6a0898dee7e2f8fe5966f489bccdc72b500959bb9a9`.
The checker verifies CometBFT voting power, signed header, fixed validator set,
chain, height and state hash. The publisher also rejects headers older than 120
seconds or over 30 seconds in the future, rollback, conflicting same-height state
and implicit chain changes. It compares against the cloud's last accepted head.

Every 120 seconds, it uploads the immutable certificate first and updates the
status pointer last. The UI reports header age; after 180 seconds it says that
updates are paused and shows the last accepted snapshot. It keeps 32 recent
certificates; archived experiment histories live separately in the repository.
Failure to fetch or verify never falls back to unsigned host telemetry. Ledger
membership does not imply present process liveness. The cloud needs no access to
local node RPCs, signing keys, GPUs or container runtime sockets.

Publication checks on the research bundles validate the archived checksum lists,
the original 15-record Ed25519 lineage and the diagnostic attestation. Numerical
success and failure are reported separately. Those metadata checks do not rerun
the GPU experiments or reconstruct missing binary artifacts.

Uploads authenticate with the local signed-in Entra identity, granted Blob Data
Contributor on `$web` only. A storage account key was used in memory during setup
to enable static hosting; it is not saved by this publisher. No app credentials
are embedded in the site. The HTTPS origin is the platform's default storage
hostname. See Microsoft's [RBAC guidance](https://learn.microsoft.com/en-us/azure/storage/blobs/assign-azure-role-data-access).

## Running and updating

```powershell
.venv/Scripts/python.exe scripts/build_public_site.py
.venv/Scripts/python.exe scripts/azure/public_site.py deploy
.venv/Scripts/python.exe scripts/azure/public_site.py push
& ./scripts/public_site_server.ps1 start
& ./scripts/public_site_server.ps1 status
& ./scripts/public_site_server.ps1 stop
```

The background publisher uses the current Windows session and Azure sign-in. It
does not restart after a Windows reboot or refresh an expired interactive login
on its own. Its logs and process fence live in `artifacts/public-site/` and are
not committed. Laptop sleep, expired authentication or loss of quorum leaves the
cloud's last accepted view intact and visibly stale. Local fine-tuning work is
published as experiment evidence only after its own checks and explicit release.

Only files named in the generated site manifest are uploaded. Participant
databases, session tokens, model APIs and operator controls are absent. This
public site is a demonstration and evidence surface, not a participant collection
server. The fixed-advice collection pilot remains on local port 8885. A separate
review-only study site lets the research team preview the flow
without collecting responses.

Further hardening: independent publication verifiers/operators, client-side
certificate checking, immutable release signing and a separate deployment
identity. Browser display currently trusts the HTTPS publication service and
offers certificate downloads for outside verification; it does not itself run
CometBFT verification. Azure serving availability is not consensus liveness.

## Console presentation

The platform presents the Collective by node role, independently of deployment
location. `site/roadmap.json` is the single UI source for completed, in-progress
and planned development milestones. Operational conditions (stale updates, failed
verification and unavailable nodes) remain visible as measured state. Evidence
cards name the exact successful checks; execution context and full receipts are
available in their details and archives.

The participant study has its own interface on port 8885 and a separate cloud
review site. Platform pages and
participant pages contain no navigation links to each other. Research protocol
material is not listed in the platform UI or its publication manifest.
