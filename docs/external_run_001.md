# External bootstrap rehearsal: results

On 2026-10-13, the external deployment workflow completed a full-corpus rehearsal
on a separate Podman network. Eleven simulated role instances generated their
own keys in separate container mounts: four validators, two providers, four
workers and one publisher. The assembler received only public registrations.
All validators installed the same pinned genesis.

This was one physical host and one administrator. No external machines, VPN
deployment, independent operators or economic penalties were involved. The
[deployment guide](external_cluster.md) describes the commands and assumptions.

## Measured results

| Check | Observation |
|---|---|
| One validator disconnected from its network | Three connected validators continued committing |
| A second validator disconnected | Two connected validators did not advance over an eight-second observation |
| Connectivity restored | Consensus resumed and a certified worker claim committed |
| Worker left absent after claiming | Lease expired; the state recorded its signer and generation with `misconduct_proven: false` |
| RPC proxy altered application state | Public verification rejected it because the bytes differed from the certified application hash |
| One provider stopped | Workers used the remaining honest provider |
| Another endpoint served corrupted pieces with 100 ms delay | Workers rejected 955 corrupted pieces and fetched matching bytes elsewhere |
| Entire current corpus | 8 books, 1,438 chunks, 1,657,163 tokens; 52 passing shard results covering every chunk twice |
| Final disputes | 0 |
| Final availability incidents | 1 deliberately expired lease |
| Dataset release | Finalized through consensus and checked by a keyless verifier |
| Tests | 115 passed before the full run; 20 focused cases passed after the shutdown configuration fix |

Worker counters recorded **846 successful certified-state reads** and 178
certificate failures across the configured endpoints. The failure counter is not
an attribution of every failure to a specific attack; the separate forged-RPC
probe establishes rejection of tampered state. The piece counters show 955
successful piece downloads and 1,910 failed piece attempts, including deliberately
corrupted responses and the unavailable provider. No unverified RPC fallback was
enabled for these workers.

The complete exercise, including identity bootstrap, installation, failure
injection, cold pulls, certificate verification and counter capture, took
**898.744 seconds**, excluding final container shutdown. The certified dataset
snapshot was captured at 887.956 seconds. These timings are not a throughput or
scaling benchmark. Repeated attempts against an unavailable provider added
noticeable delay; bounded peer backoff is a useful subsequent improvement.

The signed state is at height **532**, certified by the header at **533**. Its
application hash is:

`8c72745f6100ddee3e7ff5f3ec73522d965c44d7cf3d24b6ef7d4432577d01d6`

The dataset release ID is:

`89e19d1c34a30770e7de816ad3dd3154fe1540eb1c77fad0c89c8ee6d98b658d`

No model training ran. A quorum certificate authenticates agreed state under the
fixed-validator assumptions; signed worker claims still do not prove computation.

## Shutdown finding and repair

The full run exposed a lifecycle issue during cleanup: Python workers running as
PID 1 could require SIGKILL after the stop timeout. The generated Compose
configuration and direct rehearsal launches now enable an init process. The
required Podman `catatonit` helper was installed on the test host.

A separate follow-up check started the original worker image with `--init`,
confirmed the worker and verifier subprocess beneath the init process, then
stopped it in **3.016 seconds**. Exit code 143 indicates SIGTERM; no forced kill
was used. The 20 focused bootstrap/client/incident tests passed after this
configuration change. The full corpus was not rerun after that launch-only fix.
The final runtime image is available locally as `localhost/dawl-cluster:external-v2`.

The host has no Compose provider installed, so generated Compose files were not
executed through a Compose engine. The rehearsal used direct Podman commands.
Linux host-network deployment on actual mesh addresses remains to be tested.

## Reproducibility and public evidence

| Item | Pin |
|---|---|
| Full-corpus source commit | `c1b00ec20fb46fb059b8e56efb868e8f024daa50` |
| Executed image ID | `dc23c76b40dbfa6ab8d754caf60ca2ed7586b37087131f89cadb8baae7a3af0a` |
| Executed image manifest digest | `sha256:7010c6ade47ce22356fba5486bfb42f74f52a49207c0e2eddc855fb6720231fa` |
| Executed Python implementation | `51747883230fb6d9ea994874217c7152a4cc85fbfea2e7c94a71f4b571c70bcf` |
| Chain ID | `dawl-external-lab-001` |
| Genesis SHA-256 | `3208c3a13a4a586151954370f453c551f3a677399cf379c29e158fa9ed16c6fe` |
| Network manifest SHA-256 | `26c52e792bfc64ef99e92e96058becfdea3889441a72a41eff66515c2e88a7e5` |

[The public archive](results/external-run-001/) contains bootstrap metadata,
phase observations, the public network bundle, certificate, worker counters,
shutdown check and test output. Its [artifact index](results/external-run-001/artifact-index.json)
hashes each saved file. It contains no operator private keys or live databases.
Keep a trusted copy of the pins: an unsigned index alone is not a trust anchor.

All rehearsal containers are stopped and their state retained under
`/var/lib/dawl/external-lab-001`. The original cluster-v2 remains running separately.
Its exact host-worker source was preserved from its runtime image and checked
against its original implementation pin; see its [restart instructions](cluster.md).

The remaining validation boundary is an independently operated multi-host
deployment, followed by controlled latency/bandwidth/partition measurements.
Live malicious-validator equivocation was not injected here. Consensus evidence
forwarding and worker-conflict policy were tested at the application boundary;
economic punishment, dynamic membership and dispute resolution remain separate work.
