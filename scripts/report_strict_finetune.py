"""Package only measured strict-run results; exclude raw books, weights and keys."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def report(root,out):
    if out.exists():raise ValueError('Report already exists')
    release=read(root/'release.json');verified=read(root/'release-verification.json')
    if not verified['passed'] or verified['release_sha256']!=digest(root/'release.json'):raise ValueError('Unverified release')
    s=read(root/'training-summary.json');r=read(root/'replay-verification.json');merge=read(root/'merge-verification.json')
    out.mkdir(parents=True);evidence=out/'evidence';evidence.mkdir()
    for name in release['files']:
        path=root/name
        if path.suffix not in ('.json','.jsonl','.py','.sh','.txt') or name.startswith(('exports/','checkpoints/','replay-checkpoints/')):continue
        target=evidence/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    for name in ('operator.public.key','release.json','release-attestation.json','release-verification.json'):shutil.copyfile(root/name,evidence/name)
    events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    rows=[e for e in events if e['event']=='training']
    fig,axes=plt.subplots(1,2,figsize=(10,3.7),layout='constrained')
    axes[0].plot([e['step'] for e in rows],[e['loss'] for e in rows],color='#177965');axes[0].set(title='New strict run: observed training loss',xlabel='Optimizer update',ylabel='Next-token loss')
    for offset,values,color,label in [(-.18,s['baseline']['test'],'#849ca6','Base'),(.18,s['selected_evaluation']['test'],'#177965','Selected')]:
        bars=axes[1].bar([i+offset for i in range(2)],[values[d]['nll'] for d in ('economics','literature')],.34,color=color,label=label)
        axes[1].bar_label(bars,fmt='%.3f',padding=3)
    axes[1].set_xticks([0,1],['Economics','Literature']);axes[1].set(title='Frozen held-out test',ylabel='Negative log likelihood',ylim=(0,4.5));axes[1].legend(frameon=False)
    fig.savefig(out/'strict-results.png',dpi=180);plt.close(fig)
    checks='\n'.join(f"| {c['checkpoint']} | {c['optimizer_steps']} | {c['exact_tensors']}/{c['tensor_count']} | {c['relative_l2']:.9g} | {c['max_abs_difference']:.9g} |" for c in r['checks'])
    tests='\n'.join(f"| {d} | {s['baseline']['test'][d]['nll']:.6f} | {s['selected_evaluation']['test'][d]['nll']:.6f} |" for d in ('economics','literature'))
    probes='\n'.join(f"| {d} | {s['baseline']['probes'][d]['correct']}/{s['baseline']['probes'][d]['total']} | {s['selected_evaluation']['probes'][d]['correct']}/{s['selected_evaluation']['probes'][d]['total']} |" for d in ('economics','general'))
    text=f'''# Strict full-epoch fine-tuning and fresh-process replay

Recorded 14 October 2026. **Replay outcome: {r['status']}.** This is a new
Qwen3-0.6B lineage (`finetune-strict-002`), not a relabeling of the original model.
The original first-epoch mismatch and the later 12-update diagnostic are preserved.

## Frozen experiment

Same pinned Qwen revision, 16 preserved books and tokenizer as the original run.
One complete epoch: **{s['epochs'][-1]['optimizer_steps']} AdamW updates**, **{s['tokens_seen']:,}
next-token targets**, {s['effective_epochs']:.2f} corpus passes. Both LoRA A and B are trained.
Strict-math policy: deterministic algorithms fail on unsupported operations,
math SDPA only, explicit nonfused/nonforeach optimizer and clipping, no TF32 or
reduced-precision reductions, CUBLAS_WORKSPACE_CONFIG=:4096:8.

Recipe schema 3 explicitly pins one epoch; its learning-rate schedule therefore
spans one epoch, unlike the original three-epoch schedule. This tests full-epoch
repeatability of a new strict recipe, not identical weights to the old epoch 1.
Runtime 005 uses the original 002 dependencies plus the pinned execution fix.
Image ID: `{(root/'runtime-image-id.txt').read_text().strip()}`.

Training elapsed {s['training_wall_seconds']/60:.2f} minutes including evaluation;
fresh-process replay elapsed {r['wall_seconds']/60:.2f} minutes. RTX 5090 Laptop GPU,
CUDA 12.8; same physical GPU/runtime/operator. These are not cross-hardware results.
The user authorized CPU/GPU through 21:30 Oslo; the orchestration applies a hard
container deadline and the session retains its shorter internal budget.

## Replay at the full epoch boundary

| Boundary | Updates | Identical tensors | Relative L2 | Maximum absolute difference |
| --- | ---: | ---: | ---: | ---: |
{checks}

The verifier starts in a fresh process from the frozen base and recorded recipe,
reconstructs initialization and replays all updates. It does not initialize from
the trained adapter or optimizer. The resulting candidate tensors are retained.
Tensor equality is distinct from serialized-file equality: both checkpoint file
hashes are recorded, including their serialization metadata. No epsilon is used.

## Data and export checks

CPU source-to-token replay matched **{release['retokenized_matching_windows']} windows**.
The separate CPU merge checker matched **{merge['checked_tensors']}/{merge['checked_tensors']}
exported tensors**, including {merge['adapted_linear_maps']} adapted maps.
The signed release manifest and all {verified['checked_files']} bound files were rechecked.
Selected checkpoint: `{s['selected_evaluation']['selected']}`. The merged model is executable
in Transformers. This release does not claim an Ollama import or Oracle certification.

## Descriptive evaluation

| Held-out domain | Base NLL | Selected NLL |
| --- | ---: | ---: |
{tests}

| Small fixed probe set | Base | Selected |
| --- | ---: | ---: |
{probes}

![Recorded training and held-out evaluation](strict-results.png)

These small probes are descriptive; training verification is separate from answer
quality. No participant outcomes were collected or inferred by this experiment.

## Rechecking

Operator public key: `{release['public_key']}`.
Release SHA256: `{verified['release_sha256']}`.
Signed training lineage head: `{release['lineage_head']}`.

The public package includes metadata, recipes, implementations, signatures,
receipts and measurements. Raw corpus and large model/checkpoint tensors stay in
the preserved local experiment. Their hashes are bound in `evidence/release.json`.
For full binary checks, provide those files as well. Runtime source is preserved
from image 005, separately from later application edits. One local operator signed
both the training lineage and the release; this is no independent organizational
attestation and does not extend the upstream model's pretraining lineage.
'''
    (out/'README.md').write_text(text,encoding='utf-8')
    manifest={p.relative_to(out).as_posix():digest(p) for p in out.rglob('*') if p.is_file()}
    (out/'sha256-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with zipfile.ZipFile(out.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as z:
        for p in out.rglob('*'):
            if p.is_file():z.write(p,p.relative_to(out.parent))
    print({'report':str(out),'replay':r['status'],'files':len(manifest)})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();report(a.root,a.out)
