"""Generate measured training results from exported signed evidence."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'scripts'))
from verify_training import verify

root=ROOT/'docs/results/training-run-001'
verified=verify(root)
read=lambda n:json.loads((root/n).read_bytes())
stage=read('stage.json'); b=stage['body']; prepared=read('prepared.json')
availability=read('availability.json')
if (availability['verified'] is not True or availability['stage']!=stage['id']
        or availability['release']!=verified['release'] or availability['sole_source']!=b['participants'][1]['url']):
    raise ValueError('availability evidence belongs to a different release')
final=[v['body'] for v in read('completions.json')]; updates=read('updates.json'); audit=read('audit.json')['body']
payload={}
for name in ('peer-0','peer-1','auditor'):
    transfers=[json.loads(line) for line in (root/f'{name}-transfers.jsonl').read_bytes().splitlines()]
    payload[name]={'bytes':sum(t['artifact']['bytes'] for t in transfers),'objects':len(transfers),
                   'origins':sorted({t['peer'] for t in transfers})}
summary={'verification':verified,'prepared':prepared,'artifact_transfers':payload,'availability':availability,
    'evaluation':{'baseline':final[0]['baseline'],'final':audit['evaluation']},
    'training_seconds':[sum(r[i]['body']['seconds'] for r in updates) for i in range(2)],
    'adapter_exchange_bytes':b['initial_adapter']['bytes']*len(updates)*2,
    'scope':'One local stage with two CUDA containers sharing one physical GPU; fixed admitted membership; sampled numerical audit. No distributed speedup or general model quality claim.'}
(root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
(root/'verification.json').write_text(json.dumps(verified,indent=2)+'\n',encoding='utf-8')
rows='\n'.join(f"| {s} | {final[0]['baseline'][s]['loss']:.6f} | {audit['evaluation'][s]['loss']:.6f} | {audit['evaluation'][s]['target_tokens']:,} |" for s in ('train','validation','test'))
text=f'''# Distributed training run 001

**Completed:** two separate training peers continued the real pinned SmolLM2-135M
model, contributed {verified['signed_contributions']} signed adapter updates,
independently computed {verified['rounds']} matching checkpoints and published the
same audited stage release. A separate auditor reproduced all aggregation and
replayed two post-commit selected training contributions byte-for-byte.

The [architecture and protocol](../../distributed_training.md) document the
stage lifecycle, mathematical contract, recovery rules and future work.

## Measured work

| Measurement | Result |
|---|---:|
| Training peers | 2 |
| Physical GPUs | 1 shared RTX 5090 Laptop GPU |
| Source books | {prepared['books']} |
| Training / validation / test books | 11 / 1 / 1 |
| Training rounds | {verified['rounds']} |
| Local optimizer steps per peer | {b['recipe']['rounds']*b['recipe']['local_steps']} |
| Distinct allocated training windows | {verified['unique_training_windows']:,} |
| Actual next-token training targets | {verified['target_tokens']:,} |
| Updated trainable parameters | {prepared['trainable_parameters']:,} |
| FP32 adapter payload per contribution | {prepared['adapter_bytes']:,} bytes |
| Total peer-to-peer adapter payload | {summary['adapter_exchange_bytes']:,} bytes |
| Peer 0 / peer 1 local-training time | {summary['training_seconds'][0]:.2f} / {summary['training_seconds'][1]:.2f} s |
| Changed shared checkpoints | {verified['changed_checkpoints']} |

This is a bounded partial corpus pass. It is not a full epoch or full-model
fine-tune: the pretrained base and A projections stayed frozen, and B adapters
in all query/value attention projections were trained. Times above sum the
reported local optimizer work and exclude input retrieval, evaluation, peer
waiting and the intentional interruption. The two GPU containers contend for
one device, so these timings do not measure network scaling.

Both peers independently pulled their initial artifacts into their own stores.
Peer 0 received {payload['peer-0']['bytes']:,} bytes of successful object bodies;
peer 1 received {payload['peer-1']['bytes']:,}. These totals include initial model,
source/token provenance and updates. The original BF16 pretrained checkpoint was
{b['parent_model']['bytes']:,} bytes; repeated training rounds exchanged the small
FP32 adapters. The adapter total is payload, excluding signatures, HTTP framing,
polling, startup downloads and auditing. No WAN compression measurement is implied.

## Recovery and independent publication

Peer 0 deliberately exited after writing its round-1 contribution. Peer 1 could
not finalize that round without the missing checkpoint acceptance. On restart,
peer 0 reused its two durable contributions and re-verified the previous round,
then both peers completed the same parent-linked history. It reused
{final[0]['reused_updates_this_process']} updates in the resumed process.
The exported process logs record the interruption and subsequent progress.

The original Cortex was stopped before the cold auditor started. The auditor
received {payload['auditor']['bytes']:,} object bytes from the training replicas,
including the base model, data and all adapter candidates. Its recorded origins
were {', '.join(payload['auditor']['origins'])}. Both trainers retain complete
publishable composite checkpoints and identical weight ledgers. There is no
designated sole weight publisher.

After release, peer 0 was also stopped. A fresh public consumer then downloaded
and verified the complete parent model, fixed projection, trained adapter,
configuration and tokenizer using **only peer 1**: {availability['artifact_bytes']:,}
bytes in {availability['objects']} objects. See [availability.json](availability.json).
Together, these two cold downloads exercise both training peers as independent
artifact publishers. All temporary pilot services were stopped after the checks;
their native state and public evidence remain available for restart.

The auditor verified every signature, work allocation, parent link, replica and
weighted aggregate. It then selected two contributions using a fresh nonce and
the already committed final head. Both full local training replays matched the
original adapter hashes exactly. Those samples are recorded in
[audit.json](audit.json); they do not prove all {verified['signed_contributions']} training claims.
Only after this audit did both peers sign the [stage release](release.json).

## Evaluation

Each measurement uses the same 16 pinned windows per split. Loss is mean
next-token cross entropy over valid targets. The auditor independently reproduced
both peers' final evaluation exactly.

| Split | Base loss | Final loss | Evaluated targets |
|---|---:|---:|---:|
{rows}

Validation book 1342 and test book 84 were excluded from all training allocations.
The parent model may already have encountered these public books during its
pretraining. These limited measurements check execution and detect gross regressions;
they do not establish broad model improvement or downstream task quality.

## Provenance and acceptance boundaries

- Stage: `{stage['id']}`.
- Token Blueprint: `{b['token_blueprint']}`.
- Imported parent checkpoint: `{b['parent_model']['sha256']}`.
- Source revision: `{b['upstream']['revision']}`.
- Final checkpoint: `{audit['checkpoint']}`.
- Audited release: `{verified['release']}`.
- Executed source digest: `{b['implementation_sha256']}`.

The source token Blueprint has the historical Oracle certificate. Each trainer
and the auditor separately checked that quorum and replayed source tokenization.
The new training history is a **local, unanimously signed branch**, with a pinned
auditor and a separate audited stage release. It has not been admitted into the
live Oracle chain. The two trainers and auditor belong to the same operator.

The common parent, recipe, runtime, deterministic work schedule, code identity,
per-contribution adapter, round aggregates, sampled replay and release signatures
are retained. This distinguishes a declared training effort from the work that
was independently replayed. Missing-peer liveness, dynamic admission, decentralized
audit randomness, stale updates, poisoning resistance and wider hardware numerical
tolerances remain open. See the architecture document for the next scaling tests.

## Verify and reproduce

```powershell
.venv\\Scripts\\python.exe scripts/verify_training.py
```

This offline check verifies signed claims, complete coverage of the announced
stage and all parent/release bindings. It does not execute tensor operations.
The numerical audit was performed by `training_peer.py audit`, using a cold
separate node. Public evidence is in [summary.json](summary.json),
[chain.json](chain.json), [updates.json](updates.json), [completions.json](completions.json),
[verification.json](verification.json) and the signed audit/release.
The native content-addressed stores remain under
`/var/lib/dawl/training-001/state`; private keys are not exported.

The runbook in [distributed_training.md](../../distributed_training.md) gives the
image builds and lifecycle commands. A fresh run requires fresh directories and
identities. The final model is executable as the pinned base checkpoint plus the
fixed A and final B artifacts; it has not been packaged as a general chat model.
'''
(root/'README.md').write_text(text,encoding='utf-8')
print(root/'README.md')
