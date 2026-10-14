"""Build an o200k GPU addendum from verified, signed execution evidence."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_profile_gpu import verify

root=ROOT/'docs/results/o200k-gpu-run-001'
report=json.loads((root/'report.json').read_bytes()); result=report['profiles'][0]
verified=verify(root,ROOT/'docs/results/profile-testnet-002',['o200k_base'])
(root/'verification.json').write_text(json.dumps(verified,indent=2)+'\n',encoding='utf-8')
binding=json.loads((root/'o200k_base-table-import.json').read_bytes())
old_root=ROOT/'docs/results/profile-gpu-run-001'
verify(old_root,ROOT/'docs/results/profile-testnet-002')
old=json.loads((old_root/'report.json').read_bytes())
combined={'schema':'hypertrix.gpu-coverage-summary/1','source_runs':['profile-gpu-run-001','o200k-gpu-run-001'],
          'ordinary_tokenizer_profiles':6,'books_per_profile':13,
          'profile_frags':old['profile_frags']+report['profile_frags'],
          'dense_bytes':old['dense_bytes']+report['dense_bytes'],
          'http_payload_bytes':old['http_payload_bytes']+report['http_payload_bytes'],
          'scope':'Sum of two separate local runs, including cold tables in each profile. Not one simultaneous six-node throughput test.'}
(root/'combined-coverage.json').write_text(json.dumps(combined,indent=2)+'\n',encoding='utf-8')
compat=result['compatibility'];upstream=binding['upstream'];shape=binding['upstream_tensor']['shape']
text=f'''# o200k GPU verification with GPT-OSS input weights

**Passed:** all {result['books']} books, {result['frags']:,} Frags and {result['tokens']:,}
ordinary o200k tokens. The GPU reconstructed **{result['dense_bytes']/1e9:.3f} GB** of
dense F32 input vectors from **{result['http_payload_bytes']/1e9:.3f} GB** of cold HTTP
object payload, including the full canonical input table. Every Frag matched both
NumPy replay and its earlier signed CPU vector commitment byte-for-byte.

This expands GPU coverage to **six ordinary-tokenizer profiles**, totaling
**{combined['profile_frags']:,} profile/Frag executions** and **{combined['dense_bytes']/1e9:.3f} GB**
reconstructed across this run and the [five-profile run](../profile-gpu-run-001/README.md).
The prior five-profile evidence is preserved as a separate historical run.

## What makes this a valid o200k test

The actual pretrained input table comes from
[openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b/tree/{upstream['revision']}).
GPT-OSS uses o200k_harmony; its
[official tokenizer definition](https://github.com/openai/gpt-oss/blob/main/gpt_oss/tokenizer.py)
shares the ordinary o200k mergeable vocabulary and splitting pattern and adds
Harmony control tokens. We verified every one of the **{compat['ordinary_rows_verified']:,}
ordinary byte-piece-to-token-ID mappings** against the pinned checkpoint tokenizer,
including the exact splitting expression and normalization policy.

We then disabled added-token/control matching in an independent Hugging Face
tokenizer reference and compared its ordinary encoding against the stored o200k
token IDs for **every corpus Frag**, during both CPU preparation and GPU replay.
Literal strings such as `<|start|>` and `<|return|>` remain text. No chat template,
control token insertion, Harmony rendering or model inference is performed.
This is an input-embedding compatibility test, not a claim that o200k_base and
o200k_harmony are interchangeable for chat. See the
[Harmony format documentation](https://developers.openai.com/cookbook/articles/openai-harmony).

The original certified tokenizer-only profile remains:
`{result['source_profile_id']}`.
The new model-bound execution profile is:
`{result['profile_id']}`.
It has {shape[0]:,} rows and {shape[1]:,} dimensions, covering the ordinary IDs plus
the model's additional rows. The o200k rank artifact and ordinary encoding contract
are unchanged. Token provenance continues to point to the existing certified
Blueprint `{result['token_blueprint']}`; a version-2 vector commitment explicitly
binds that source profile to the new execution profile and imported weights.
The new execution profile and vector receipts are **local diagnostics**, not newly
registered or certified Oracle vector jobs.

## Measured execution

| Measurement | Result |
|---|---:|
| Books / Frags | {result['books']} / {result['frags']:,} |
| Ordinary tokens | {result['tokens']:,} |
| Canonical input table | {result['table_bytes']:,} bytes |
| Reconstructed dense vectors | {result['dense_bytes']:,} bytes |
| Successfully fetched HTTP object bodies | {result['http_payload_bytes']:,} bytes |
| Dense F32 / received payload | {result['dense_to_http_payload_ratio']:.2f}× |
| Fewer payload bytes than dense F32 transfer | {result['avoided_dense_transfer_percent']:.2f}% |
| Cold GPU verification pipeline | {result['total_seconds']:.2f} s |
| GPU reconstruction plus CPU comparison and hashing | {result['gpu_replay_including_cpu_comparison_hashing_seconds']:.2f} s |
| GPU gather/host-return time | {result['gpu_roundtrip_seconds']:.2f} s |
| CUDA kernel-event time | {result['kernel_ms']:.2f} ms |
| GPU launches | {result['gpu_launches']:,} |

Hardware: {result['runtime']['device']}; CUDA driver API
{result['runtime']['driver_api_version']}; CPU fallback disabled.
The separate Cortex and GPU containers shared one local WSL host. The GPU had a
fresh CAS cache and fetched tokenizer/config assets, raw source, token manifests,
signed lineage, compatibility evidence, vector commitments and chunked weights.
Its read-only bootstrap contained public identities and the historical certificate,
not corpus or matrix files. HTTP framing is excluded from byte counts.
The fixed input table is counted in the cold payload above; it could be reused for
later corpora, but no warm-cache measurement is claimed here.

Initial checkpoint acquisition and prior CPU commitment preparation are separate
from the timed cold GPU verification pipeline. The complete required safetensors
shard was **{upstream['bytes']:,} bytes**, SHA-256
`{upstream['sha256']}`. That complete file was checked before extracting
`{upstream['tensor']}` ({binding['upstream_tensor']['dtype']}). Its other model tensors
were not imported or executed. The input table is exactly widened to F32; the
transfer comparison therefore concerns dense F32 arrays, not compressed/BF16 output.
Timings are observations from one local run, not a WAN benchmark or model-quality comparison.

## Verification chain

```mermaid
flowchart TD
    O[Historical Oracle certificate] --> S[Original o200k source profile and token Blueprint]
    S --> R[Retokenize every Frag with pinned o200k]
    U[Pinned GPT-OSS config and tokenizer] --> C[Check all ordinary token IDs and full corpus encoding]
    R --> C
    C --> P[New immutable model-bound execution profile]
    W[SHA-verified pretrained checkpoint] --> T[Chunked canonical F32 input table]
    P --> A[Signed CPU vector commitment v2]
    T --> A
    S --> A
    A --> G[Independent cold HTTP pull and CUDA Gather]
    G --> V[Bit-exact NumPy and commitment verification]
    V --> D[Signed diagnostic receipt v2]
```

For each Frag f, V_f = E_GPT-OSS[t_f, :]. The compatibility check establishes
which table rows each ordinary token ID addresses. CPU and GPU both reproduce
SHA-256 of the row-major little-endian F32 bytes. The commitment binds the source
profile, token Blueprint, execution profile, import identity and each Frag's token
and vector descriptors. The receipt signs the same identities and the observed
coverage. Swapping a profile, Blueprint, rank mapping or table breaks a checked binding.

The table bound was raised from 2 GiB to 4 GiB, while preserving the kernel's
32-bit element-index constraint, 64 MiB reusable output buffer and device-memory
preflight check. A separate labelled synthetic fixture tested indexing past the
2 GiB boundary and the final model row on actual CUDA. The corpus measurement
above uses real pretrained weights, not that fixture.

Tests cover changed vocabulary rows, altered split patterns, undersized model
vocabularies, config tampering, signed source/execution-profile substitutions and
literal control strings. The corpus run also rejected a false vector hash,
flipped GPU output bit, negative/out-of-range IDs and fractional indices.
See [verification.json](verification.json) for the exported signature and coverage
check. Those checks are not a proof of remote computation; both diagnostic keys
belong to one operator. The historical source quorum is checked separately in the
runner. Source authenticity and upstream training provenance remain separate claims.

## Reproduce

```powershell
.venv\\Scripts\\python.exe scripts/fetch_o200k_model.py
.venv\\Scripts\\python.exe scripts/fetch_profile_weight_ranges.py gpt-oss-20b
wsl -d kali-linux -u root -- podman build --network none -f Dockerfile.profile-gpu -t localhost/hypertrix-o200k-gpu:v1 .
wsl -d kali-linux -u root -- sh scripts/o200k_gpu_pilot.sh init
wsl -d kali-linux -u root -- sh scripts/o200k_gpu_pilot.sh prepare
wsl -d kali-linux -u root -- sh scripts/o200k_gpu_pilot.sh provider
wsl -d kali-linux -u root -- sh scripts/o200k_gpu_pilot.sh replay
wsl -d kali-linux -u root -- sh scripts/o200k_gpu_pilot.sh finish
.venv\\Scripts\\python.exe scripts/verify_profile_gpu.py --root docs/results/o200k-gpu-run-001 --names o200k_base
.venv\\Scripts\\python.exe scripts/report_o200k_gpu.py
```

The native state directory `/var/lib/dawl/o200k-gpu-001` is created once. Use a fresh
state directory and result destination for another execution. Input artifacts come
from the preceding local profile testnet's public provider store. Private validator
keys are neither mounted nor exported. The GPU container exits after its work, and
the finish step stops the temporary Cortex at `127.70.0.29:8900`.

Public evidence includes [report.json](report.json), the full signed CPU commitment,
GPU receipt and table-import manifest, [combined coverage](combined-coverage.json)
and implementation pins. Reproducing the numerical result requires the pinned
checkpoint and corpus artifacts; offline receipt verification checks signatures,
bindings and coverage without pretending to redo the numerical work.
'''
(root/'README.md').write_text(text,encoding='utf-8')
print(root/'README.md')
