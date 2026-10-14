"""Render the Seed results explanation from checked numerical evidence."""
import argparse
import json
from pathlib import Path
import re
from verify_seed_release import verify


def write_results(root):
    root=Path(root);report=json.loads((root/'report.json').read_bytes())
    verify(root,report,metadata_only=True)
    s=report['summary'];comparison=json.loads((root/'comparison.json').read_bytes())
    recipe=json.loads((root/'dense/recipe.json').read_bytes())
    bp=json.loads((root/'dataset/blueprint.json').read_bytes())
    labels={'seed1_dense':'Seed I dense','seed1_moe':'Seed I MoE','seed2_dense':'Seed II dense'}
    domains=('code','economics','literature','norwegian')
    rows=['| Model | Parameters | Code | Economics | Literature | Norwegian |',
          '|---|---:|---:|---:|---:|---:|']
    for name,model in comparison['models'].items():
        rows.append('| '+labels[name]+f" | {model['parameters']:,} | "+' | '.join(f"{model['test'][d]['nll']:.4f}" for d in domains)+' |')
    intervals=', '.join(f"{r['start']:,}–{r['end']:,}" for r in report['replay_intervals'])
    sample_sizes='; '.join(f"{d}: {comparison['models']['seed2_dense']['test'][d]['targets']:,} targets" for d in domains)
    text=f'''# Hypertrix Seed II — corpus and model growth

Seed II trained **{s['parameters']:,} parameters from random initialization** on a
versioned corpus of **{bp['documents']:,} documents**. The selected checkpoint is
step **{s['selected_step']:,}**. The run finished with status **{s['status']}** after
**{s['completed_steps']:,} / {s['planned_steps']:,} updates**, processing
**{s['target_tokens_this_session']:,} nonpadding target tokens** in
**{s['elapsed_seconds']/60:.2f} minutes** including training-time evaluation and saving.

![Learning curves and paired evaluation](learning.png)

## Data and architecture

The corpus retains 8,183 public repository files and 16 Gutenberg books and adds
378 texts from the National Library's
[bokselskap.no 2023 distribution](https://www.nb.no/sprakbanken/ressurskatalog/oai-nb-no-sbr-81/),
also listed on [data.norge.no](https://data.norge.no/en/datasets/246b8421-5aa8-3efe-ac4b-3b05fe243ef3/bokselskapno-2023).
Both catalogues declare CC0. Four unresolved multi-author anthologies are
quarantined. Reviewed author aliases, coauthors and edition families stay in one
split. The source lock preserves the original XML and text hashes, distribution,
licence, catalogue snapshots and transformation identity.

Each full training epoch contains **{bp['arrays']['train']['targets']:,} targets**.
The original 4,096-token byte-BPE vocabulary is unchanged. Seed II has 12 layers,
width 512, eight attention heads and a 1,408-wide SwiGLU feed-forward block, with
RMSNorm, RoPE and tied input/output weights. It uses AdamW and a batch of
{recipe['batch_size']} windows of up to 512 tokens. The recipe requests
{recipe['epochs']} epochs with {recipe['runtime']['precision']} precision and
{recipe['runtime']['attention']} attention. Master weights and optimizer state are FP32.

## Paired held-out evaluation

The table reports token-weighted negative log-likelihood within each domain's
fixed sample of 32 held-out windows; **lower is better**. All three checkpoints
use the identical tokenizer and test windows. Test sample sizes are {sample_sizes}.
Checkpoint selection uses validation, not this table.

{chr(10).join(rows)}

The models differ in size, architecture, corpus and training exposure. This is a
comparison of complete configurations; it does not isolate the effect of any one
change. The four-domain mean must not be compared directly with Seed I's original
three-domain mean. These are sampled evaluations, not full-holdout scores.

## Reproduction and verification

The data replay reconstructed all 378 added XML texts and their token windows,
checked all 8,199 parent documents and unchanged parent arrays, and verified
disjoint source families and exact content hashes. A separate exact-paragraph
audit found no cross-split matches of at least 200 Unicode characters in the
Norwegian domain. Broader near-duplicate detection is outside that audit.

Fresh processes replayed update intervals **{intervals}** on the same GPU and
pinned runtime. Every interval reproduced **{report['replay_tensor_count']} /
{report['replay_tensor_count']} model-state tensors exactly**, with maximum absolute
difference zero and identical AdamW state. These receipts cover the specified
intervals. The public artifact checker checks hashes and signatures; it does not
run those numerical replays again.

The signed lineage binds random initialization, configuration, recipe,
implementation, imported local dependencies, checkpoint descriptors, tensor-file
hashes, final summary and replay receipts. The release seal additionally binds
this explanation, figures and supporting files. Signatures authenticate the
recorded operator key. This is single-GPU full-model training with separate
replay processes; it is not multi-host full-model backpropagation or an Oracle
certificate.

Blueprint SHA-256: `{report['blueprint_sha256']}`

Release operator key: `{report['operator_public_key']}`

From the repository, verify the downloaded and extracted metadata bundle:

```console
python scripts/verify_seed_release.py --root PATH_TO_BUNDLE --expected-public-key {report['operator_public_key']}
```

The bundle contains metadata, code, public keys and receipts. Full numerical
replay also needs the source/token arrays and checkpoint tensors identified by
the manifests. Those artifacts and private signing keys remain in experiment
storage. `docs/seed_expansion.md` documents the methodology and source admission.

## Fixed continuation prompts

These are the actual outputs from the paired comparison: temperature 0, up to 64
new tokens, 512-token context, and the same three prompts for every model. They
are completions rather than instruction-following chat responses.
'''
    for name,model in comparison['models'].items():
        text+='\n### '+labels[name]+'\n'
        for item in model['continuations']:
            content='PROMPT\n'+item['prompt']+'\n\nCONTINUATION\n'+item['text']
            fence='`'*max(3,1+max((len(x) for x in re.findall(r'`+',content)),default=0))
            text+='\n'+fence+'text\n'+content+'\n'+fence+'\n'
    (root/'README.md').write_text(text,encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);a=p.parse_args();write_results(a.release)
