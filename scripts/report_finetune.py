"""Publish measured local results and a shareable public evidence bundle."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REPO=Path(__file__).resolve().parents[1]


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def build(root,out):
    if out.exists():raise ValueError('Preserve existing run report; choose a new output directory')
    s=read(root/'training-summary.json');v=read(root/'verification.json')
    r=read(root/'replay-verification.json');c=read(root/'control-result.json')
    package=read(root/'serving-package.json');session=read(root/'gpu-session.json')
    merge=read(root/'merge-verification.json')
    initialization=read(root/'replay-initialization-diagnostic.json')
    kernel=read(root/'replay-kernel-diagnostic.json')
    events=[json.loads(l) for l in (root/'events.jsonl').read_text().splitlines() if l]
    out.mkdir(parents=True);evidence=out/'evidence';evidence.mkdir()
    names=['source-lock.json','base-lock.json','dataset-lock.json','recipe.json','probes-lock.json',
           'gpu-session.json','lineage.jsonl','operator.public.key','baseline.json','selected-evaluation.json',
           'training-summary.json','control-result.json','control-codebook.json','exports.json','verification.json',
           'replay-verification.json','serving-package.json','runtime-image-002-id.txt','verifier-replay.py',
           'merge-verification.json','merge-verification-single-round.json','merge-checker-single-round.py',
           'conversion-inputs-'+s['selected_evaluation']['selected']+'.json']
    for pattern in ('replay-checkpoint-*.json','replay-init-*.json','replay-*-diagnostic.json','verifier-initialization.py','verifier-kernels.py'):
        names += [p.name for p in root.glob(pattern)]
    for name in names:shutil.copyfile(root/name,evidence/name)
    shutil.copytree(root/'serving-evidence',evidence/'serving-evidence')
    shutil.copytree(root/'lab-interface',evidence/'lab-interface')
    current_interface=evidence/'interface-current';current_interface.mkdir()
    for name in ('trust_lab.html','trust_lab.py','trust_provenance.py','trust_provenance.js','trust_provenance.css'):
        shutil.copyfile(REPO/'scripts'/name,current_interface/name)
    shutil.copytree(root/'verifiers',evidence/'verifiers')
    if (root/'execution-notes').exists():shutil.copytree(root/'execution-notes',evidence/'execution-notes')
    shutil.copyfile(REPO/'docs/finetuning_lab.md',out/'methodology.md')
    training=[e for e in events if e['event']=='training']
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'axes.titleweight':'bold','figure.facecolor':'white'})
    fig,axes=plt.subplots(1,3,figsize=(13,3.8),layout='constrained')
    for domain,color,label in [('economics','#2269a8','Economics'),('literature','#a95d22','Literature')]:
        values=[s['baseline']['validation'][domain]['nll']]+[e['validation'][domain]['nll'] for e in s['epochs']]
        axes[0].plot(range(len(values)),values,'o-',color=color,label=label)
    axes[0].set(title='Validation across epochs',xlabel='Epoch (0 = baseline)',ylabel='Negative log likelihood')
    axes[0].set_xticks(range(len(s['epochs'])+1));axes[0].legend(frameon=False)
    baseline=s['baseline']['test'];selected=s['selected_evaluation']['test']
    for offset,values,color,label in [(-.17,baseline,'#7794b0','Baseline'),(.17,selected,'#2269a8','Selected')]:
        bars=axes[1].bar([i+offset for i in range(2)],[values[d]['nll'] for d in ('economics','literature')],.32,color=color,label=label)
        axes[1].bar_label(bars,fmt='%.3f',fontsize=9,padding=3)
    axes[1].set_xticks([0,1],['Economics','Literature']);axes[1].set(title='Frozen test subset',ylabel='Negative log likelihood',ylim=(0,4.6));axes[1].legend(frameon=False)
    bars=axes[2].bar(['Baseline','Separate\ncontrol adapter'],[c['before']['correct'],c['after']['correct']],color=['#7794b0','#2269a8'])
    axes[2].bar_label(bars,fmt='%d / 16',padding=3);axes[2].set(title='Invented association control',ylabel='Exact matches on held-out wording',ylim=(0,18))
    fig.savefig(out/'measured-results.png',dpi=180);fig.savefig(out/'measured-results.svg');plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,3),layout='constrained')
    ax.plot([e['step'] for e in training],[e['loss'] for e in training],color='#2269a8',linewidth=1.5)
    ax.set(title='Observed training loss',xlabel='Optimizer update',ylabel='Next-token loss')
    fig.savefig(out/'training-loss.png',dpi=180);plt.close(fig)
    test_rows='\n'.join(f"| {d.title()} | {baseline[d]['nll']:.6f} | {selected[d]['nll']:.6f} | {baseline[d]['perplexity']:.3f} | {selected[d]['perplexity']:.3f} |" for d in ('economics','literature'))
    probe_rows='\n'.join(f"| {d.title()} | {s['baseline']['probes'][d]['correct']}/{s['baseline']['probes'][d]['total']} | {s['selected_evaluation']['probes'][d]['correct']}/{s['selected_evaluation']['probes'][d]['total']} |" for d in ('economics','general'))
    replay_rows='\n'.join(f"| {x['checkpoint']} | {x['exact_tensors']}/{x['tensor_count']} | {x['max_abs_difference']:.6g} | {x['relative_l2']:.6g} |" for x in r['checks'])
    epochs=s['effective_epochs'];updates=s['epochs'][-1]['optimizer_steps']
    deadline=datetime.fromtimestamp(session['deadline_unix'],timezone.utc).isoformat()
    readme=f'''# Local economics fine-tuning and provenance pilot

Recorded 14 October 2026. This is a local calibration branch of Hypertrix. The existing Oracle testnet was kept running; these Qwen checkpoints have local signed records and are not Oracle-certified.

**Training and local serving completed. Artifact, source-to-token and CPU merge checks passed. Exact GPU training replay failed at the first epoch boundary; the later two boundaries were not compared.** The original model and every failure receipt are preserved. Improved held-out language-model loss did not translate into improved scores on the small economics question set.

## What ran

Official Qwen3-0.6B, pinned to `c1899de289a04d12100db370d81485cdf75e47ca`, continued on the existing 13 books plus Smith, Ricardo and Bagehot: **16 books, {epochs:.2f} epochs, {updates} optimizer updates and {s['tokens_seen']:,} next-token targets processed**. LoRA updated 10,092,544 parameters on the **NVIDIA RTX 5090 Laptop GPU**, using CUDA 12.8. Peak allocated tensor memory was {s['peak_vram_bytes']/2**30:.2f} GiB. Both local comparison models are served as BF16 GGUF through Ollama.

The starting weights were published by Qwen under Apache-2.0. Earlier development is represented by the provider's documentation; the detailed local trail starts at that published checkpoint. The [methodology](methodology.md) records the source selection, split rule, optimization and selection procedure.

## Measured outcomes

Selected checkpoint: **{s['selected_evaluation']['selected']}**, chosen using economics validation NLL with the frozen literature guard. The test subset did not select the checkpoint. Each test domain contains 32,768 targets from 64 preselected windows.

| Test domain | Baseline NLL | Selected NLL | Baseline perplexity | Selected perplexity |
|---|---:|---:|---:|---:|
{test_rows}

![Measured validation, test and control results](measured-results.png)

These results measure next-token prediction on held-out book segments. They do not establish that the books were absent from Qwen's pretraining, and they do not identify which passage caused an answer. This is one run with no confidence interval or multi-seed estimate.

| Fixed closed-book probes | Baseline | Selected |
|---|---:|---:|
{probe_rows}

The small probes are descriptive, not a broad economics benchmark. The original prompts, options, scores and generated explanations remain in the evidence.

The **separate invented-association control** scored **{c['before']['correct']}/{c['before']['total']} before and {c['after']['correct']}/{c['after']['total']} after {c['steps']} updates** on held-out question wording. Its adapter is separate from the economics model. It tests a small codebook memorization task rather than general reasoning.

![Recorded optimizer losses](training-loss.png)

## What was checked

The separate CPU checker reports **{v['signed_records']} valid signed records**, **{v['source_books']} preserved books**, **{v['source_segments']} source segments** and **{v['retokenized_matching_windows']:,} token windows independently rebuilt with matching token IDs**. It also rechecked checkpoint/export hashes and the signed serving package. Serving package verified: **{v['serving_package_verified']}**.

Fresh-process training replay status: **{r['status']}**. Every completed comparison uses exact tensor equality, with no tolerance substituted for a failure.

| Checkpoint boundary | Exactly matching tensors | Maximum absolute difference | Relative L2 |
|---|---:|---:|---:|
{replay_rows}

The replay starts from the base weights and public recipe rather than loading the trainer's adapter or optimizer state. It uses the same GPU, runtime and operator. Thus it is computational replication, not an independent institution or heterogeneous-hardware validation. The original GPU deadline was **{deadline}** and was not extended. The trainer reports {s['training_wall_seconds']/60:.1f} minutes elapsed, including pauses; the last recorded replay progress was {r['wall_seconds']/60:.1f} minutes. The replay was stopped after the first mismatch to investigate within that budget. Its intentional exit 137 is recorded. **This is a failed exact check, not merely an unfinished check.** Epochs 2 and 3 remain untested by replay.

The initialization diagnostic reproduced the initial adapter tensors and CPU/CUDA random-number states exactly (`full_vs_abbreviated_identical={initialization['full_vs_abbreviated_identical']}`). A second diagnostic ran two ten-update prefixes with the same initialization, seed, recipe and data order. It found **{kernel['final_exact_tensors']}/{kernel['final_tensor_count']} exactly matching final tensors**, with maximum absolute difference **{kernel['final_max_abs_difference']:.9g}**. Deterministic algorithms were enabled: **{kernel['deterministic_algorithms_enabled']}**. This demonstrates non-repeatability of the current execution path even on this GPU; it does not isolate the responsible operation or prove that all of the full-epoch difference has one cause.

The next exact-mode recipe needs explicit deterministic execution settings, pinned backend choices and a short repeatability gate before a long run. PyTorch documents that controlling random seeds and avoiding nondeterministic algorithms are separate requirements ([PyTorch 2.11 reproducibility](https://docs.pytorch.org/docs/2.11/notes/randomness.html)). Such a recipe would be a new experiment. This run's mismatch is not retrospectively accepted using an epsilon threshold.

The signed serving package connects the selected adapter's exported weights, actual GGUF bytes, Ollama digest, case definitions and nine recorded model responses. CPU merging rounds LoRA updates into BF16; the serving artifact is explicitly recorded as a derivative. Hash verification binds those bytes and records; it is not a semantic proof of the converter implementation.

A separate CPU merge check passed: **{merge['checked_tensors']}/{merge['checked_tensors']} exported tensors**, including **{merge['adapted_linear_maps']} adapted linear maps / {merge['adapter_tensors_consumed']} adapter tensors**. Its first attempt assumed a single final rounding and failed on the adapted maps. Inspection of the pinned PEFT implementation established that safe merge rounds the update to BF16 before adding it to the BF16 base. The corrected checker reproduces that specified arithmetic exactly. Both receipts and the original checker are preserved; the model weights were not changed to make the check pass.

The training runtime's tokenizer serialization initially failed in the older converter runtime. A separate conversion view pairs the unchanged, originally pinned tokenizer/config with the merged weights, preserving the signed export untouched. Its file mapping is hashed and checked. The published base's duplicate input/output embedding tensors were also byte-identical, confirming the tied-weight omission in the merged export.

Windows logged idle-triggered Modern Standby at 14:53–15:24 and 15:39–15:42 Oslo time, coinciding with stalls. A temporary Windows keep-awake request was activated for the remaining pilot, with the original deadline unchanged. These standby intervals are recorded as execution context; they are not subtracted as an exact measurement of GPU active time.

Operator public key: `{v['trusted_operator_public_key']}`. Final signed head: `{v['lineage_head']}`.

## Live human-trust prototype

Open [Trust Lab](http://127.0.0.1:8885/) locally, or follow its link from the [Collective Console](http://127.0.0.1:8882/). Three real model conditions answer the same three documented financial cases: unchanged local Qwen weights, OpenAI GPT-5.4 mini and the selected local adaptation. These nine answers are captured once and preserved without rewriting; errors are not replaced with dummy text.

The comparison page shows available provider documentation for Qwen and OpenAI, and the local development chain for Hypertrix. The participant view explains the starting model, added books, text preparation, training, replay and recorded answer through clickable steps. It describes the evidence without an advice disclaimer or a claim that the evidence should raise trust. Technical scope remains inspectable.

The signed package preserves its original interface snapshot in `evidence/lab-interface`. Final visual inspection found that the update counter preferred the last periodic log (680) over the completed summary (684). That display-only correction is included separately in `evidence/interface-current` and this bundle's checksum manifest. It changes no model, answer, metric record or signed artifact. Trial records capture the active interface hashes.

The controlled A/B/C prototype holds the exact answer constant and varies disclosure: none, developer statement, or checkable records. The three-model comparison and before/after reveal mode are exploratory and do not isolate a causal provenance effect. Domain-expert output review, comprehension testing, recruitment, power and analysis plans remain research work; this report contains no participant-effect findings.

## Rechecking and sharing

This folder includes public metadata and frozen case responses under `evidence/`. It excludes private keys, credentials, full model weights and the raw corpus. Full binary verification uses the preserved local pilot directory `/var/lib/dawl/finetune-economics-001` in WSL. The per-file SHA256 list detects changes to this shareable copy; the Ed25519 ledger anchors its signed artifacts.

Run `scripts/verify_finetune.py --root <pilot> --trusted-public {v['trusted_operator_public_key']}` from the repository environment. The report graphs are generated from the recorded JSON, not hand-entered measurements.
'''
    (out/'README.md').write_text(readme,encoding='utf-8')
    manifest={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'sha256-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    archive=out.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(out.rglob('*')):
            if path.is_file():z.write(path,path.relative_to(out.parent))
    return {'report':str(out/'README.md'),'archive':str(archive),'replay':r['status'],'selected':s['selected_evaluation']['selected']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.root,a.out),indent=2))
