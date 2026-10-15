"""Package the completed recovery trial's explicit public evidence directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def package(source, output):
    if output.exists(): raise ValueError('fresh evidence export required')
    def read(name): return json.loads((source/name).read_text())
    result=read('verification.json'); trial=read('trial.json')
    if not result['exact_state_match'] or result['stages']!={'a':'complete','b':'cancelled','c':'complete'}:
        raise ValueError('completed, checked trial required')
    paths=[p for p in source.rglob('*') if p.is_file()]
    for path in paths:
        relative=path.relative_to(source)
        if path.is_symlink() or 'private' in path.name or path.suffix in ('.pem','.sqlite','.safetensors','.pyc'):
            raise ValueError('nonpublic evidence file: '+str(relative))
        if not (path.suffix in ('.py','.sh','.json','.jsonl') or relative.parts[0] in ('objects','src')):
            raise ValueError('unexpected evidence type: '+str(relative))
    shutil.copytree(source,output)
    state=read('training-certificate.json')['state']; ids={k:read(f'stage-{k}.json')['id'] for k in 'abc'}
    stages={k:state['training_stages'][v] for k,v in ids.items()}
    a,b,c=(stages[k] for k in 'abc')
    availability=read('availability.json')
    if (availability['checkpoint']!=c['head'] or availability['adapter']!=c['adapter']
            or availability['uncached_http_hash_match'] is not True):raise ValueError('availability binding differs')
    reject=a['recovery_history'][0]; report=a['failure_reports'][reject['report']]
    replacement=state['training_jobs'][report['job']]['contribution']
    same_adapter=replacement['adapter']==reject['contribution']['adapter']
    checkpoints=state['training_checkpoints']
    end_a=max(v['height'] for v in checkpoints[a['head']]['receipts'].values())
    end_c=max(v['height'] for v in checkpoints[c['head']]['receipts'].values())
    deadline=b['round_started_height']+b['body']['recovery']['round_timeout_blocks']
    rows=[('Stage A registered',a['height']),('Unavailable update reported',report['height']),
          ('Owner rejects exact update',reject['height']),('Replacement shard and full quorum',end_a),
          ('Stage B registered',b['height']),('One verifier approves',next(iter(b['votes'].values()))['height']),
          ('Fixed round deadline',deadline),('Stage B cancelled',b['recovery_history'][-1]['height']),
          ('Replacement stage C registered',c['height']),('Stage C full quorum',end_c)]
    book_head=next(x for x in state['profile_releases'][a['body']['profile_id']] if x['id']==a['body']['token_blueprint'])
    targets=checkpoints[a['head']]['body']['target_tokens']
    text=f'''# Live training recovery — trial 003

Completed {trial['completed_utc']}. Protocol 6 was activated on four fresh
CometBFT validators. Real SmolLM2-135M GPU training recovered from a withheld
submitted update and from a missing verifier. Two checkpoints were promoted,
each carrying the complete two-verifier replay quorum.

## Measured outcomes

| Test | Observed result |
| --- | --- |
| Source lineage | {len(book_head['body']['books'])} books, {len(book_head['body']['receipts'])} fresh CPU audit receipts |
| GPU contributions | {result['actual_gpu_contributions']}: five in completed/recovered stage attempts, two in the cancelled attempt |
| Numerical replay | {result['numerical_replay_checks']} bit-exact contribution checks; eight support the two promoted checkpoints, two belong to the cancelled attempt |
| Withheld update | Trainer stopped after its signed submission; verifier emitted a content-addressed failure receipt; owner rejected the exact contribution; another trainer completed that shard under a new lease generation |
| Deterministic replacement | Replacement adapter matches the original withheld commitment: {same_adapter} |
| Missing verifier | One approval remained insufficient; cancellation after the fixed {b['body']['recovery']['round_timeout_blocks']}-block deadline preserved the last verified parent |
| Replacement | New immutable stage explicitly changed verifier roster and referenced the cancelled stage; both verifiers replayed both contributions |
| Late vote | Correctly signed approval against the cancelled stage rejected online; rejection reproduced offline |
| Ledger reconstruction | {result['signed_transactions_replayed']} signed transactions across {result['blocks_replayed']} blocks, byte-identical certified final state |
| Retained availability | All trial GPU workers stopped; a fresh HTTP client retrieved {availability['bytes_received']:,} bytes from the Cortex and matched the certified final adapter hash |
| Training compute | {result['training_compute_seconds']:.3f} seconds summed across seven contribution computations; excludes bootstrap, replay, transfer and consensus waits |

Each stage used two shards, two local SGD steps per shard, batch size two and
sequence length 128. Each attempt processed the same eight training windows
({targets} next-token targets); stages B and C extend A's verified adapter. This
is a recovery experiment with a small pinned workload, not a full corpus epoch
or a model-quality comparison. The base model and LoRA A projection are frozen;
184,320 FP32 B values are trained and reduced in canonical shard order.

## Sequence on the ledger

| Event | Committed block height |
| --- | --- |
'''+''.join(f'| {name} | {height} |\n' for name,height in rows)+f'''
```mermaid
flowchart LR
  A[Stage A: submitted update withheld] --> F[Signed failure receipt]
  F --> R[Owner rejection and new lease generation]
  R --> H[Both verifiers replay: checkpoint A]
  H --> B[Stage B: one verifier absent]
  B --> T[Fixed deadline: cancel and retain A]
  T --> C[New stage C: explicit replacement roster]
  C --> V[Both verifiers replay: checkpoint C]
```

Recovery cannot vote a checkpoint into existence. The promotion condition
remains `approved verifier keys = immutable stage verifier keys`, with matching
candidate commitments. Rejection increments a recovery revision and discards
earlier votes. Cancellation releases the branch slot while retaining the
verified head, submitted work, votes and failure history.

## Independently checkable evidence

The archive contains genesis, the final quorum certificate, every signed
application transaction, the frozen application source, executable manifests,
measured execution environment, stage definitions, failure objects and worker
replay records. `verify_recovery_pilot.py` checks the certificate, replays the
entire ledger, reproduces the late-vote rejection and verifies the links between
recovery events, submitted artifacts, replay receipts and promoted checkpoints.

Genesis pin (obtain through a trusted channel):

`{trial['genesis_sha256']}`

Final checkpoint:

`{c['head']}`

With Python dependencies and the repository's `check-certificate` binary on PATH:

```sh
python scripts/verify_recovery_pilot.py --root . --genesis-sha256 {trial['genesis_sha256']}
```

Full numerical replay additionally needs the corpus and model objects identified
by the manifests. They remain in the trial's node stores; bulk model tensors
and node private keys are excluded from this public archive. Running the archive
checker verifies signatures, acceptance policy and evidence bindings; it does
not itself rerun the GPU training.

## Deployment boundary

Separate containers, keys, databases and artifact stores shared one RTX 5090
Laptop GPU and one operator. The faults were real process stops and an absent
verifier, controlled by the trial driver. This establishes supervised recovery
on actual workers. Independent operators, WAN partitions, automatic replacement
selection, durable availability challenges and malicious-input quality gates
remain separate validation work.

The source-audit bootstrap retains the explicitly labelled synthetic v1/v2
fixture. The 13-book profile, training computations and numerical replays are
real. Initial setup attempts 001 and 002 stopped before training because their
provider routes violated existing allowlists; their private deployment records
are retained. Trial 003 used admitted routes without relaxing those policies.

The historical protocol-4 demo, identities and certificates were unchanged.
The GPU trial has finished; completed compute containers are stopped and their
artifacts retained. The protocol-6 validators and provider remain available.

The first archive attempt refused to overwrite an immutable completion
observation with a newer height. The exporter now uses height-qualified
observation filenames; both observations are retained. This packaging correction
did not alter the activated application or rerun training. The archive includes
the executed helper, corrected exporter, shell driver and deployment image ID.
'''
    (output/'README.md').write_text(text,encoding='utf-8')
    (output/'timeline.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    hashes={p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob('*')) if p.is_file()}
    (output/'sha256-manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    print(json.dumps({'files':len(hashes),'output':str(output),'checkpoint':c['head']}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();package(a.source,a.output)
