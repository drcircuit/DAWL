# Hybrid GPU reconstruction run 001

Executed 14 October 2026 Oslo time, on the existing Hypertrix chain. The
`dawlh1013185918-nodes-gpu-0` container remains running and watches for a changed
certified Blueprint. Status: `http://127.0.0.1:8883/`. It shares the existing pilot
deadline, 14 October 20:41:52 Oslo. No Azure GPU resources were added.

## Node and method

One separate WSL/Podman container uses the NVIDIA GeForce RTX 5090 Laptop GPU,
with a 2-CPU/2-GiB container limit and a fresh Ed25519 key. It mounts only its own
state, read-only public bootstrap and WSL driver libraries, `/dev/dxg`, and its
own telemetry directory. Host networking remains part of this invited pilot.

The node verifies Oracle state against the original genesis, discovers providers
through signed DHT records, checks the complete released lineage, downloads
hash-addressed artifacts into a fresh cache, retokenizes the source, reconstructs
each Frag on CUDA, compares every byte with NumPy, and checks the committed vector
SHA-256. All 29 current peers returned its signed unexpired AUDITOR advertisement.
This advertisement grants no audit or validator admission. The report is a signed
diagnostic; no execution lease or consensus receipt was submitted.

The fixed PTX kernel uses integer addressing and 32-bit loads/stores to gather
float32 table rows. There is no floating-point arithmetic, downloaded executable
code, framework fallback or training. The same table stays resident on the GPU;
bounded buffers hold one Frag at a time. Raw vectors are discarded after checking.

## Observed results

| Measurement | Result |
| --- | ---: |
| Books / Frags / tokens | 13 / 1,807 / 2,061,330 |
| CUDA launches over corpus | 1,807 |
| Vector bytes reconstructed | 6,332,405,760 |
| GPU vs CPU vs ledger mismatch | 0 |
| End-to-end replay, including fetch and source checks | 66.577 s |
| CUDA event kernel time, summed | 211.634 ms |
| CUDA host/device round trip, summed | 6.664 s |
| NumPy reference lookup, summed | 3.323 s |
| Downloaded object payload bytes | 172,517,031 |
| Explicit GPU buffers, excluding driver/context overhead | 204,786,688 bytes |
| Persisted dense sequence-vector bytes | 0 |

The distributed-system result is compact transfer followed by verified local
reconstruction: 172.5 MB of fetched objects yielded 6.33 GB of dense vectors in
66.6 seconds. This is 36.7 times fewer application payload bytes (97.3% less)
than transferring the uncompressed dense representation alone. The total includes
one shared-table download into the new node cache. This benefit comes from the
representation and reconstruction strategy and is available on CPU as well as GPU.

The run also verifies hardware execution and exact GPU/CPU/hash agreement. Local
lookup acceleration is a separate question: the measured CUDA round trips took
longer than NumPy lookup. Kernel events exclude transfers; CUDA round trips exclude
final serialization; NumPy includes it. These scopes are not an isolated benchmark.
The total run includes both implementations and their checks. Downloads count
application object payloads, not all wire traffic. Local providers served the data;
WAN speedup and comparison with compressed dense vectors still need measurement.

CUDA fixtures passed first/last and repeated indices, partial blocks, 16,384-token
boundary, signed zero and finite subnormal bit patterns. Six invalid index cases
and a flipped output bit were rejected. Four focused tests passed, including real
GPU lineage replay and rejection of an incorrectly committed vector index with a
valid signature and object hash. No CPU fallback was used.

## Evidence and reproduction

Public evidence: `docs/results/hybrid-gpu-run-001/`. The signed diagnostic includes
per-book results, kernel/script hashes, runtime, input-state hash and precise scope.
The captured input state was verified at height 2413; a later independent
certificate at height 2516 confirms the same released Blueprint. The public
certificate and discovery records are historical observations with timestamps.

Implementation: `scripts/cuda_gather.py`, `scripts/gpu_reconstruction_node.py`,
`Dockerfile.gpu`. Core `src/` bytes and the live consensus implementation pin were
unchanged. Build with `podman build -f Dockerfile.gpu -t localhost/dawl-gpu:node-v1 .`.
The WSL-specific operator `scripts/gpu_operator.py` refuses to replace an existing
container or extend the recorded deadline. `scripts/azure/enable_gpu_peer.py`
allows exactly the private discovery endpoint before the operator opens a separate
authenticated SSH route. Existing SSH sessions retain their old forwarding policy.
If replacing all tunnels, close `gpu.sock` before the main updated topology takes
over its route. `scripts/gpu_report.py` exports only explicitly named public files.

The new backend is experimental and WSL/NVIDIA-specific. It does not establish
general cross-vendor determinism for arithmetic-heavy model training.
