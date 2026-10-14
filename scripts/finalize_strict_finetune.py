"""Seal a finished strict run after full artifact and CPU merge verification.

No pass is inferred from subprocess exit alone. Both failed and successful replay
receipts can be preserved; publication must describe the recorded outcome.
"""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sys
import math
import shutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts'))
from dawl_node.common import read_json,write_json,load_key,sign,verify_signature
from dawl_finetune.data import file_hash
from verify_finetune import verify


def check_replay_artifacts(root, summary, replay):
    """Check the retained candidate tensors against the receipt in a CPU process."""
    import torch
    from safetensors.torch import load_file
    torch.set_num_threads(4)
    for field,name in [('recipe_sha256','recipe.json'),('dataset_lock_sha256','dataset-lock.json'),
                       ('verifier_script_sha256','verifier-replay.py')]:
        if replay[field]!=file_hash(root/name):raise ValueError('Replay input binding differs')
    epochs={e['name']:e for e in summary['epochs']};checks=[]
    if len({c['checkpoint'] for c in replay['checks']})!=len(replay['checks']):raise ValueError('Duplicate replay boundary')
    for receipt in replay['checks']:
        name=receipt['checkpoint']
        if name not in epochs or receipt['optimizer_steps']!=epochs[name]['optimizer_steps']:raise ValueError('Replay boundary differs')
        expected=root/'checkpoints'/name/'adapter_model.safetensors'
        candidate=(root/receipt['replayed_checkpoint_file']).resolve()
        if not candidate.is_relative_to((root/'replay-checkpoints').resolve()):raise ValueError('Invalid replay candidate')
        if file_hash(expected)!=receipt['expected_checkpoint_sha256'] or file_hash(candidate)!=receipt['replayed_checkpoint_sha256']:raise ValueError('Replay checkpoint hash differs')
        targets=load_file(str(expected));actual=load_file(str(candidate))
        if targets.keys()!=actual.keys():raise ValueError('Replay tensor schema differs')
        exact=0;maximum=0.;error=0.;reference=0.
        for key,target in targets.items():
            value=actual[key]
            if target.shape!=value.shape or target.dtype!=value.dtype or not torch.isfinite(value).all() or not torch.isfinite(target).all():raise ValueError('Invalid replay tensor')
            exact+=int(torch.equal(target,value));delta=(value.float()-target.float()).double()
            maximum=max(maximum,float(delta.abs().max()));error+=float(delta.square().sum());reference+=float(target.double().square().sum())
        relative=math.sqrt(error/max(reference,1e-30))
        if (receipt['exact_tensors']!=exact or receipt['tensor_count']!=len(targets) or
            receipt['exact_match']!=(exact==len(targets)) or
            not math.isclose(receipt['relative_l2'],relative,rel_tol=1e-12,abs_tol=1e-15) or
            not math.isclose(receipt['max_abs_difference'],maximum,rel_tol=1e-12,abs_tol=1e-15)):
            raise ValueError('Replay receipt differs from retained tensors')
        checks.append({'checkpoint':name,'exact_tensors':exact,'tensor_count':len(targets),
                       'relative_l2':relative,'max_abs_difference':maximum})
    result={'passed':True,'checks':checks,'scope':'Independent CPU check of retained trainer and replay candidate tensors against their recorded receipt.'}
    write_json(root/'replay-artifact-verification.json',result)


def finalize(root):
    if (root/'release.json').exists():raise ValueError('Release exists; preserve it')
    public=(root/'operator.public.key').read_text().strip()
    summary=read_json(root/'training-summary.json');replay=read_json(root/'replay-verification.json')
    merge=read_json(root/'merge-verification.json');recipe=read_json(root/'recipe.json')
    if not merge['passed']:raise ValueError('CPU export check failed')
    if len(summary['epochs'])!=recipe['epochs'] or not all(e['complete_epoch'] for e in summary['epochs']):raise ValueError('Training did not finish the frozen plan')
    if replay['status']=='exact-match' and (len(replay['checks'])!=len(summary['epochs']) or not all(c['exact_match'] for c in replay['checks'])):raise ValueError('Replay pass contradicts receipts')
    check_replay_artifacts(root,summary,replay)
    checked=verify(root,public);write_json(root/'verification.json',checked)
    implementation=root/'verification-implementation';implementation.mkdir()
    for script in ('finalize_strict_finetune.py','verify_finetune.py','verify_finetune_merge.py'):
        shutil.copyfile(ROOT/'scripts'/script,implementation/script)
    names=['source-lock.json','base-lock.json','dataset-lock.json','segments.jsonl','recipe.json','probes-lock.json',
           'gpu-session.json','baseline.json','training-summary.json','selected-evaluation.json','exports.json',
           'verification.json','merge-verification.json','replay-verification.json','replay-artifact-verification.json','lineage.jsonl',
           'runtime-image-id.txt','runtime-packages.json','verifier-replay.py','orchestrator.sh','events.jsonl']
    files={}
    def include(path):files[path.relative_to(root).as_posix()]={'sha256':file_hash(path),'bytes':path.stat().st_size}
    for name in names:include(root/name)
    for directory in ('checkpoints','replay-checkpoints','exports','implementation','verification-implementation'):
        for path in sorted((root/directory).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:include(path)
    result={'schema':'hypertrix.strict-finetune-release/1','completed_utc':datetime.now(timezone.utc).isoformat(),
            'public_key':public,'lineage_head':checked['lineage_head'],'replay_status':replay['status'],
            'epochs':summary['effective_epochs'],'optimizer_steps':summary['epochs'][-1]['optimizer_steps'],
            'source_books':checked['source_books'],'retokenized_matching_windows':checked['retokenized_matching_windows'],
            'files':files,'scope':'New Qwen local lineage. Same GPU, runtime and operator; no Oracle certificate, cross-hardware claim or participant results.'}
    write_json(root/'release.json',result)
    attestation=sign({'schema':'hypertrix.strict-release-attestation/1','signer':public,'result_sha256':file_hash(root/'release.json'),
                      'lineage_head':checked['lineage_head']},load_key(root/'operator.private.pem'))
    write_json(root/'release-attestation.json',attestation)
    body=verify_signature(attestation,public)
    if body['result_sha256']!=file_hash(root/'release.json'):raise ValueError('Release signature differs')
    for name,meta in files.items():
        if file_hash(root/name)!=meta['sha256'] or (root/name).stat().st_size!=meta['bytes']:raise ValueError('Release bytes changed')
    write_json(root/'release-verification.json',{'passed':True,'checked_files':len(files),'release_sha256':body['result_sha256'],
              'public_key':public,'verified_utc':datetime.now(timezone.utc).isoformat(),'scope':'Signature and all release files including preserved replay tensor candidates; computation outcome remains separate.'})
    print({'release_verified':True,'replay_status':replay['status'],'files':len(files)},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/pilot'));a=p.parse_args();finalize(a.root)
