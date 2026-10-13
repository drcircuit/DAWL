# Azure AKS pilot

Status: deployment tooling prepared and locally checked. An online run is only
established by a populated `artifacts/azure-pilot-001/results/report.json` and its
verified certificate. Template compilation and unit tests are not cloud validation.

The approved first pilot has a **EUR 50 budget and a four-hour window**, including
provisioning. It uses AKS instead of individually administered VMs. The managed
control plane uses the Free tier; its node machines, disks and network are billable.
The initial region is West Europe, subject to subscription quota and capacity checks.

## Placement and identities

| Component | Count | Placement / state |
|---|---:|---|
| Consensus validators | 4 | Required host anti-affinity, separate Azure Disk PVCs |
| Artifact providers | 2 | Required host anti-affinity, separately verified corpus copies |
| CPU auditors | 20 | One admitted key and durable SQLite/outbox per pod/PVC |
| Publisher | 1 | On-demand publication via its own installed identity |
| AKS system hosts | 4 × D4as v5 | Validators, providers, publisher and Kubernetes services |
| AKS audit hosts | 7 × D8as v5 | Three 2-CPU auditors per host, reserving capacity for system overhead |

This is 27 DAWL nodes on 11 managed machines, requesting 72 provisioned vCPUs.
Twenty auditors are five times the local four-auditor membership; this is **not a
fivefold measured throughput claim**. Four validators retain the existing three-vote
quorum. The four validators run on four different AKS hosts, but all infrastructure
shares one Azure subscription, region and administrator. This pilot does not
demonstrate independent operators or tolerance to an entire regional outage.

Each role is a single-replica StatefulSet. An 8-GiB managed disk contains its key,
consensus signing state or worker outbox and artifact cache. ReadWriteOnce disks,
StatefulSet ownership and `OnDelete` updates reduce accidental concurrent writers.
Do not force-delete a partitioned validator pod, clone a validator PVC, or run a
replacement until the original process is fenced. Disk access mode alone is not
a cryptographic defense against duplicate signers. PVCs use Retain on ordinary
workload deletion; deleting the dedicated Azure resource groups is final cleanup.

Stable ClusterIP Services in `10.240.1.0/24` are registered as each node's advertised
address. Processes explicitly bind `0.0.0.0` inside their pod; private services
route to the current pod IP. The original private-mesh deployment retains its
address-specific bind by default. Application ports are not Internet-exposed.
The Kubernetes API permits only the operator's current public IPv4 `/32` and
requires Entra authentication. A changed operator IP requires an authorized update.

Cilium NetworkPolicy limits pod traffic to namespace peers on RPC/P2P/provider
ports and cluster DNS. Pods run non-root with a read-only image, dropped
capabilities and no Kubernetes API token. Azure Container Registry is private
to authenticated clients; AKS uses its kubelet managed identity to pull the
image. The registry image digest and source digest are pinned in the network bundle.

The pod launcher creates a key only in an unused PVC directory, then waits for
the bootstrap ConfigMap. The assembler receives only signed public registrations
and the existing keyless corpus provider. A changed network pin or identity fails
closed on restart. The shared bootstrap is controlled by this pilot operator;
it is not an independent multi-operator genesis ceremony.

## Cost and deadline

`scripts/azure/prices.py` queries the official unauthenticated EUR retail API.
It excludes Windows, Spot and Cloud Services meters, saves the selected meters,
and adds 25% compute contingency, EUR 10 ancillary allowance and a further 25%
tax allowance. The initial estimate was **EUR 13.18 compute / EUR 33.09 envelope**
for four hours. Actual subscription pricing, taxes and metered use can differ.
The ancillary allowance is a reserve, not a quoted line-item bill.

`control.py` refuses deployment above EUR 50, stale estimates, insufficient
72-vCPU regional/family quota, restricted SKUs, or an existing resource group.
It records the recovery scope before mutation. Preflight queries are read-only
apart from selecting the requested Azure CLI subscription.
ARM validation runs before provisioning. If infrastructure creation fails, the
controller deletes only the newly created dedicated resource group; no DAWL
identity has been generated at that point. A failed rollback requires inspection.

An Azure Logic Apps workflow checks every five minutes and requests `AKS stop`
at **3 hours 45 minutes after deployment begins**, leaving time for shutdown in
the four-hour envelope. Its managed identity has Contributor permission only on
this AKS resource. No subscription-wide shutdown permission is granted. The
workflow must be checked after deployment, including its role assignment and
trigger state. Failure to provision it is a deployment failure requiring recovery.
It is a cost safeguard, not a guaranteed monetary cap. Stop completion is asynchronous.

Stopping AKS does not remove retained disks, registry, IP/load balancer or the
deadline workflow. Capture evidence, stop, verify stopped state, and delete the
dedicated pilot resource group when its ephemeral signing state is no longer
needed. Verify the AKS-managed node resource group is also gone. Never restart a
stopped pilot beyond its agreed window without a new approved run budget.

## Commands for this workspace

The controller targets Windows with the current WSL `kali-linux` Podman build
host and its preserved `/var/lib/dawl/cluster-v2/provider-0`. It is not yet a
generic CI/CD installer. Runtime identity/installation commands remain portable.
Use the same locally built image for assembly and registry upload; do not rebuild
between those operations, because literal Python source bytes are pinned.

```powershell
# Local Azure CLI installation used in this workspace
$azPython = '.\artifacts\azure-tools\Scripts\python.exe'
& $azPython -m azure.cli login --use-device-code

# Subscription must be supplied explicitly; do not put credentials in files.
.\.venv\Scripts\python.exe scripts/azure/control.py preflight --subscription <subscription-id>

# Review artifacts/azure-pilot-001/preflight.json and the template before spending.
.\.venv\Scripts\python.exe scripts/azure/control.py deploy --subscription <subscription-id> --apply

# Match kubectl to the deployed AKS minor version before launch (supported skew ±1).
& $azPython -m azure.cli aks install-cli --client-version <server-version> `
  --install-location artifacts/azure-tools/kubectl.exe `
  --kubelogin-install-location artifacts/azure-tools/kubelogin.exe

wsl -d kali-linux -u root --exec podman build -f Dockerfile.cluster -t localhost/dawl-cluster:aks-v1 .
.\.venv\Scripts\python.exe scripts/azure/launch.py
.\.venv\Scripts\python.exe scripts/azure/observe.py

.\.venv\Scripts\python.exe scripts/azure/control.py stop --subscription <subscription-id> --apply
.\.venv\Scripts\python.exe scripts/azure/control.py status --subscription <subscription-id>

# Destructive: after exporting evidence and choosing to discard this pilot's keys.
.\.venv\Scripts\python.exe scripts/azure/control.py delete --subscription <subscription-id> --apply
```

Deploying requires permission to create the listed resources and assign scoped
roles. Contributor alone cannot create RBAC assignments. Review subscription
policies and provider registration failures rather than loosening them automatically.
An interrupted launch leaves its marker, keys and PVCs intact and refuses a fresh
bootstrap over them; inspect and resume the affected step deliberately.

## What the pilot establishes

The first run replays the same locked eight-book corpus: 1,438 chunks and 1,657,163
tokens, divided into 26 jobs with two distinct auditor attestations each. Providers
receive only allowlisted content-addressed public artifacts, checked locally and
again in their containers. Workers pull inputs and reconstruct vectors. Successful
publication requires complete coverage; a Go verifier checks its quorum-certified
state against the genesis pin. No training is executed.

`observe.py` saves certified progress, final release/certificate, pod-to-host
placement, resource usage when available and per-worker transfer/certificate
counters. Its elapsed time includes startup ordering, initial downloads and
observation delay. It must not be reported as a controlled speedup benchmark.
Only 52 audit slots exist, so 20 workers may spend substantial time pulling the
shared embedding table or waiting for consensus. Compare fresh runs with 4 and
20 workers on the same corpus and job partition before claiming useful scaling.

The manifest generator and public bootstrap accept a 40-worker design, but the
approved lifecycle controller intentionally deploys only 20. Forty workers need
14 audit hosts, a fresh membership/chain, new quota and cost review. Changing
validator membership, moving to independent operators, region failures, cloud
fault injection, autoscaling admission and model training remain separate work.

## References

- [AKS pricing tiers](https://learn.microsoft.com/en-us/azure/aks/free-standard-pricing-tiers)
- [AKS Cilium network policy](https://learn.microsoft.com/en-us/azure/aks/use-network-policies)
- [Azure Disk CSI storage](https://learn.microsoft.com/en-us/azure/aks/azure-disk-volume)
- [Stopping and starting AKS](https://learn.microsoft.com/en-us/azure/aks/start-stop-cluster)
- [Azure retail pricing API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)
- [Budget alerts do not stop consumption](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)
