"""Publish observed ablations without modifying the sealed economics pilot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from dawl_node.common import verify_signature


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
    return h.hexdigest()


def report(root,out):
    if out.exists():raise ValueError('Preserve the existing report')
    result=read(root/'replay-verification.json');public=(root/'operator.public.key').read_text().strip()
    attestation=verify_signature(read(root/'attestation.json'),public)
    if attestation['result_sha256']!=digest(root/'replay-verification.json'):raise ValueError('Attestation mismatch')
    # Verify every included metadata artifact here. Full tensor-file integrity is
    # checked separately on the Linux filesystem to avoid streaming gigabytes over UNC.
    out.mkdir(parents=True);evidence=out/'evidence';evidence.mkdir()
    for name,meta in result['files'].items():
        p=root/name
        if p.suffix not in ('.json','.jsonl','.py','.txt','.log') and name!='operator.public.key':continue
        if digest(p)!=meta['sha256'] or p.stat().st_size!=meta['bytes']:raise ValueError('Metadata changed: '+name)
        target=evidence/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    for name in ('replay-verification.json','attestation.json','operator.public.key','artifact-verification.json'):
        shutil.copyfile(root/name,evidence/name)
    shutil.copyfile(REPO/'docs/training_verification.md',out/'verification-design.md')
    source=out/'implementation';source.mkdir()
    for rel in ('src/dawl_finetune/execution.py','src/dawl_finetune/run.py','scripts/replay_finetune.py',
                'scripts/finetune_replay.sh','scripts/finetune_repeatability.sh','Dockerfile.finetune-exact',
                'tests/test_finetune_execution.py'):
        target=source/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/rel,target)
    shutil.copyfile(REPO/'artifacts/trust-lab-001/repeatability-power-guard.jsonl',evidence/'power-guard.jsonl')
    rows=result['comparisons'];legacy=next(r for r in rows if r['policy']=='legacy-auto')
    labels={'legacy-auto':'Original\nautomatic','math-only':'Math\nattention only','strict-auto':'Strict +\nautomatic','strict-math':'Strict +\nmath'}
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'})
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    bars=axes[0].bar([labels[r['policy']] for r in rows],[r['adapter_factors']['exact_tensors'] for r in rows],color=['#ba7138','#327c67','#327c67','#327c67'])
    axes[0].bar_label(bars,labels=[str(r['adapter_factors']['exact_tensors'])+' / 392' for r in rows],padding=4)
    axes[0].set(title='Exact final tensors within each pair',ylabel='Adapter tensors',ylim=(0,450))
    values=[100*legacy['adapter_factors']['relative_l2'],100*legacy['adapter_change_from_initial']['relative_l2'],100*legacy['effective_lora_delta']['relative_l2']]
    bars=axes[1].bar(['Final adapter\nparameters','Change from\ninitial parameters','Effective LoRA\nweight update'],values,color=['#7794b0','#427da9','#245c88'])
    axes[1].bar_label(bars,labels=[f'{v:.4f}%' for v in values],padding=4)
    axes[1].set(title='Original-settings pair: normalization matters',ylabel='Relative L2 distance (%)',ylim=(0,max(values)*1.25))
    fig.savefig(out/'repeatability.png',dpi=170);fig.savefig(out/'repeatability.svg');plt.close(fig)
    table=[];timings=[]
    for pair in rows:
        attempt=read(root/(pair['policy']+'-1')/'result.json')
        backend='FlashAttention' if any('flash' in op.lower() for op in attempt['attention_operations']) else 'Math attention'
        first=pair['first_observed_difference'];first_text=f"Update {first['step']}, {first['phase']}" if first else 'None in recorded checks'
        table.append(f"| {pair['policy']} | {attempt['steps']} | {backend} | {pair['adapter_factors']['exact_tensors']}/392 | {100*pair['effective_lora_delta']['relative_l2']:.6f}% | {first_text} |")
        for n in (1,2):
            a=read(root/(pair['policy']+f'-{n}')/'result.json')
            post=a['trace'][1:]
            timings.append(f"| {pair['policy']} / {n} | {sum(t['seconds'] for t in post)/len(post):.3f} | {a['wall_seconds']:.1f} |")
    b=legacy['behavior'];g=legacy['first_micro_gradients'];v=read(root/'artifact-verification.json')
    text=f'''# Why the training replay diverged

**The short-run mismatch is reproducible and fixable.** The original execution selected FlashAttention and diverged during the first backward pass. Math attention alone removed the divergence; strict settings also made the automatically selected FlashAttention path repeatable. The longer strict-math pair completed 12 optimizer updates per process with every adapter tensor identical.

The diagnostic was run on 14 October 2026 on the NVIDIA RTX 5090 Laptop GPU. It took **{result['wall_seconds']/60:.2f} minutes**, within a separate 20-minute cap. It uses the frozen pilot's model, data, initial adapter weights and software dependencies. The original three-epoch model and first-epoch mismatch are unchanged. No full-epoch strict replay or heterogeneous-hardware verification has yet been performed.

## Controlled comparisons

Each row compares two fresh processes under the same settings. There are four updates per process except the strict-math confirmation, which has twelve. These are instrumented training prefixes, not a new economics fine-tuning run.

| Policy | Updates per process | Observed attention path | Exactly equal final tensors | Effective update relative L2 | First observed divergence |
|---|---:|---|---:|---:|---|
{chr(10).join(table)}

![Exact repeatability and three different normalizations](repeatability.png)

The first original-settings pair had identical initial tensors and CPU/CUDA RNG states. Its first forward loss and sampled forward logits matched exactly. **190 of 392 adapter gradient tensors differed after the first microbatch**, before gradient clipping or the AdamW step. The maximum absolute gradient difference was **{g['max_abs']:.9g}**. The math-only intervention preserves the other execution settings, isolating attention-backend selection as the effective remedy in this test. Profiling identifies the original FlashAttention backward operation. A separate low-level kernel proof or exhaustive analysis of every later divergence is not claimed.

Strict mode enables deterministic algorithms with errors on unsupported operations, sets the cuBLAS workspace before CUDA starts, disables TF32/reduced-precision reductions, and explicitly selects optimizer/clipping implementations. Strict-auto still selected FlashAttention here. Strict-math forces math attention and is the conservative reference policy. The new [verification design](verification-design.md) explains the execution contract and why seeds alone were insufficient.

## How large were the differences?

For the **new four-update original-settings pair**:

| Quantity | Measured value |
|---|---:|
| Final adapter parameter relative L2 | {100*legacy['adapter_factors']['relative_l2']:.6f}% |
| Change-from-initial parameter relative L2 | {100*legacy['adapter_change_from_initial']['relative_l2']:.6f}% |
| Effective LoRA update relative L2 | {100*legacy['effective_lora_delta']['relative_l2']:.6f}% |
| Effective LoRA update cosine | {legacy['effective_lora_delta']['cosine']:.9f} |
| Maximum absolute adapter parameter difference | {legacy['adapter_factors']['max_abs']:.9g} |
| Maximum absolute effective-update difference | {legacy['effective_lora_delta']['max_abs']:.9g} |
| Top-1 prediction agreement on 128 positions | {100*b['top1_agreement']:.5f}% |
| Mean KL divergence on those positions (nats) | {b['mean_kl_nats']:.9g} |
| Maximum KL divergence (nats) | {b['max_kl_nats']:.9g} |
| Economics NLL difference (run 2 − run 1) | {b['nll_difference']['economics']:.9g} |
| Literature NLL difference (run 2 − run 1) | {b['nll_difference']['literature']:.9g} |

Effective changes are computed as `(alpha/r) * B @ A` in CPU FP64, one map at a time. The largest layer-relative discrepancy was {100*legacy['effective_lora_delta']['worst_maps'][0]['relative_l2']:.3f}% in `{legacy['effective_lora_delta']['worst_maps'][0]['map']}`. These early updates have small norms; both absolute and relative metrics matter. A tiny factor-relative number can be diluted by the much larger initialized factors.

The common output evaluator uses math attention. It measures two fixed validation windows per domain for NLL and the first 32 positions of each for logits. No free-text response equivalence or downstream advice-quality conclusion follows from this small sample. These measurements are not the original 228-update epoch comparison: its replay candidate was not retained. The updated verifier preserves future replay candidates for later analysis.

## Changes implemented

- New immutable recipe schema 2 with selectable strict execution policies; the legacy recipe remains supported and unchanged.
- A versioned runtime, `localhost/hypertrix-finetune:004`, based on the preserved 002 dependencies. Image ID: `4f6b8843409c25f443fd992b71274452bd253f8bc5c02d3e144a19585e949dc8`.
- A replay failure now takes precedence over a later timeout; incomplete work cannot obscure an already observed mismatch.
- Future replay boundaries save the verifier's actual adapter and its hash, including failed comparisons.
- The Trust Lab training page shows these short-run results separately from the original model's failed epoch replay. Diagnostic nodes are visible in the Console with their own evidence directory.
- Six execution-contract/runtime tests passed in the pinned runtime; six Trust Lab tests passed, including access boundaries for diagnostic evidence. These tests do not substitute for a new full-epoch replay.

The original run is not retrospectively certified by a successful diagnostic. The fixed source is prepared for a new lineage; no participant responses or original checkpoint records were changed.

## Observed timing

| Policy / process | Mean seconds per update after update 1 | Process wall seconds |
|---|---:|---:|
{chr(10).join(timings)}

These timings include copying and hashing gradients/weights. The first update additionally invokes the profiler and is excluded from the per-update mean. Runs were sequential on one laptop and may experience thermal or background-load variation. This is not a throughput benchmark. The temporary Windows system/display keep-awake guard covered the diagnostic and released when it completed. An initial output-directory permission error occurred before GPU work; ownership was corrected and the container restarted. No training attempt was discarded to obtain an exact result.

## Recommended verification approach

Retain exact replay for homogeneous cohorts with pinned execution contracts. For larger workloads, develop commitments to complete restartable states and post-commitment random interval audits. For heterogeneous branches, investigate predeclared per-layer bounds on effective updates plus separate behavioral evidence. Keep SHA256 exact in every mode; numerical agreement is a receipt relating different artifacts, not a fuzzy hash.

Cosine similarity alone accepts a scaled update. Endpoint/output similarity alone does not prove the claimed work. Full replay also does not prove which node originally performed work: a worker could copy a valid contribution. Reward and admission rules need a separate adversarial design. Checkpoint proof-of-learning literature contains both proposals and demonstrated spoofing attacks; the [design note](verification-design.md) includes those sources and the assumptions behind sampled detection probabilities.

## Evidence and reproduction

The operator signature binds the diagnostic result manifest to original lineage head `{result['source_lineage_head']}`. Operator public key: `{public}`. The separate artifact checker verified **{v['checked_files']} files** against that signed manifest, including saved tensor candidates. This establishes file integrity and attribution to the same local operator; it does not create an Oracle quorum or independent organizational verification.

`evidence/` contains the pinned diagnostic implementation, actual settings, traces, comparisons, exit receipts, logs, signature and file manifest. The large tensor snapshots remain in `/var/lib/dawl/finetune-repeatability-001`; their hashes are in the signed manifest. `implementation/` contains the subsequent integration changes, separately from the exact code used for the diagnostic. The original model directory was mounted read-only throughout the experiment.

Live view: [Trust Lab training evidence](http://127.0.0.1:8885/#training). Original experiment: [economics pilot](../finetune-economics-001/README.md).
'''
    (out/'README.md').write_text(text,encoding='utf-8')
    manifest={p.relative_to(out).as_posix():digest(p) for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'sha256-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(out.parent))
    return {'report':str(out/'README.md'),'metadata_files':len(manifest),'artifact_verification':v['passed']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(report(a.root,a.out),indent=2))
