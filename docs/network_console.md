# Network dashboards

The browser monitor and terminal dashboard focus on current nodes and their roles.
Inactive work and earlier launch attempts are in history. Raw runtime names,
addresses, hashes and resource diagnostics remain available in technical details.
The GPU view keeps device inspection separate from the main network page.

## Terminal console

From the repository in PowerShell:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-console.txt
./hypertrix-console.ps1
```

Use **1** Network, **2** Work, **3** Verification, **4** History; **J/K** page,
**P** pause/resume, **R** refresh and **Q** quit. Ctrl+C also closes cleanly.
The screen resizes with the terminal and restores the prompt on exit. Requests run
in a background thread so a slow endpoint does not block the keyboard. Disconnection
retains the last observation and clears current resource rates. Stale nodes are
marked last-seen, not online. Network strings are stripped of terminal controls.

```powershell
./hypertrix-console.ps1 -Once
./hypertrix-console.ps1 -Endpoint https://dawlh1013185918state.z1.web.core.windows.net/live/status.json
.venv/Scripts/python.exe -X utf8 scripts/hypertrix_tui.py --view verification
```

Python 3 with `requirements-console.txt` also runs on Linux/macOS. The PowerShell
launcher uses the project environment. The TUI only performs GET requests; it does
not hold node keys or schedule training. It reports the connected observer's
verification status, rather than independently rerunning the certificate checker.

## Operational counters

The current observer collects node CPU-time deltas, resident container memory,
and Linux network namespace byte counters. It deduplicates shared namespaces.
Inbound/outbound rates are separate: summing RX and TX would count a transfer at
both ends. Loopback is included because the current network uses it. A shared
namespace can also contain traffic from other processes, so these are observed
interface rates, not isolated application-goodput measurements. They are not
quorum-certified computational evidence. Per-node transfer accounting is a later
instrumentation step.

For two valid samples, `rate = (counter_now - counter_before) / elapsed_seconds`.
New or reset counters, missing samples and gaps over 60 seconds produce unavailable
rates, never fabricated zero. Memory uses one latest sample. The UI exposes the
sampling and measurement scope in details. This collector covers its selected
network containers; standalone fine-tuning histories are displayed separately.

An instrumented trainer/verifier may write `public/compute-metrics.json` in its
node directory using this contract:

```json
{
  "schema": "hypertrix.node-compute/1",
  "observed_at": "2026-10-14T18:00:00+00:00",
  "counter_epoch": "unique-process-or-counter-epoch",
  "convention": "scalar-fp-ops-fma2",
  "kind": "estimated",
  "flops_total": 1200000000
}
```

`flops_total` is a cumulative scalar floating-point operation count, with a fused
multiply-add counted as two. `kind` is `counted` for instrumented counts or
`estimated` for a documented operation model. Rates aggregate fresh, matching
counter epochs only; mixed counted/estimated inputs are identified as mixed.
The dashboard reports coverage. Currently the workers do not emit these counters,
so FLOP/s is **Not reported**. Neither GPU utilization nor tensor dimensions alone
are converted to achieved FLOP/s.

The collector can be kept running in a hidden Windows process:

```powershell
./scripts/network_collector.ps1 start
./scripts/network_collector.ps1 status
./scripts/network_collector.ps1 stop
```

This wrapper is for the current WSL network `003`. Avoid starting another collector
for that exchange directory. No machine startup registration is installed.

## Shared Console

The public Console publishes the role roster from the independently checked
certificate and pinned genesis: validators, Cortex providers, admitted trainers
and verifiers, auditors and publishers. It does not infer online status from
registration. Activity rates use differences in accepted target tokens and
accepted adapter bytes between certified snapshots no more than five minutes
apart. Accepted artifact bytes are distinct from measured transport traffic.
Raw host counters remain with the observer; public deployment continues to accept
only the verified ledger projection.

The research-team [study review](study_review.md) is hosted separately and has no
navigation links to or from either Console.
