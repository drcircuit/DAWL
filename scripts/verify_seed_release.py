"""Check Seed II public anchors, signatures and files; optionally include tensor bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_node.common import verify_signature


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def require(condition,message):
    if not condition:raise ValueError(message)


def safe(root,name):
    path=(root/name).resolve()
    require(path.is_relative_to(root.resolve()),'Path escapes artifact root')
    return path


def verify(root,report,metadata_only=False):
    root=Path(root);dataset=root/'dataset';model=root/'dense'
    bp=json.loads((dataset/'blueprint.json').read_bytes())
    require(digest(dataset/'blueprint.json')==report['blueprint_sha256'],'Blueprint anchor differs')
    for name,field in [('sources.json','sources_sha256'),('windows.json','windows_sha256'),('tokenizer.json','tokenizer_sha256'),('evaluation.json','evaluation_sha256'),('group-policy.json','group_policy_sha256')]:
        require(digest(dataset/name)==bp[field],name+' commitment differs')
    if not metadata_only:
        for split,desc in bp['arrays'].items():require(digest(dataset/(split+'.npy'))==desc['sha256'],'Token array differs')
    source=json.loads((dataset/'sources.json').read_bytes())
    for name,field in [('source-lock.json','intake_lock_sha256'),('downloads.json','catalogue_snapshots_sha256')]:
        require(digest(root/'intake'/name)==source[field],'Source intake binding differs')
    for item in json.loads((root/'intake/downloads.json').read_bytes()):
        if item['file']=='bokselskap.tar.gz':continue
        path=safe(root/'intake/catalogue-snapshots',item['file']+'.txt')
        require(digest(path)==item['sha256'] and path.stat().st_size==item['bytes'],'Catalogue snapshot differs')
    receipt=json.loads((dataset/'data-verification.json').read_bytes())
    require(receipt==report['data_verification'],'Data receipt anchor differs')
    body=verify_signature(receipt,report['data_verifier_public_key'])
    require(body['accepted'] is True and body['blueprint_sha256']==report['blueprint_sha256'],'Data replay rejected')
    require((dataset/'verifier.public.key').read_text().strip()==report['data_verifier_public_key'],'Data verifier key differs')
    require(body['parent_blueprint_sha256']==bp['parent'] and body['group_policy_sha256']==bp['group_policy_sha256'],'Data replay inputs differ')
    require(body['new_documents_reconstructed']==bp['domains']['norwegian'] and body['legacy_documents_unchanged']+body['new_documents_reconstructed']==bp['documents'],'Data replay coverage differs')
    require(all(body[k] is True for k in ('author_groups_disjoint','canonical_identities_disjoint','exact_content_splits_disjoint','legacy_arrays_and_evaluation_exact')),'Data replay checks rejected')
    require(digest(dataset/'verifier-implementation.py')==body['verifier_sha256'],'Data verifier differs')
    public=report['operator_public_key'];previous=None;records=[]
    require((model/'operator.public.key').read_text().strip()==public,'Operator key differs')
    require(digest(model/'lineage.jsonl')==report['lineage_sha256'],'Lineage anchor differs')
    for i,line in enumerate((model/'lineage.jsonl').read_text().splitlines()):
        envelope=json.loads(line);record=verify_signature(envelope,public)
        require(record['sequence']==i and record['previous']==previous,'Broken lineage')
        previous=envelope['id'];records.append(record)
    require(records and records[0]['kind']=='initialization','Missing initialization')
    initial=records[0]['payload'];require(initial['parent'] is None,'Expected scratch initialization')
    for name in ('config','recipe','implementation','dependencies'):
        require(digest(model/(name+'.json'))==initial[name+'_sha256'],name+' differs')
    for name in ('implementation','dependencies'):
        for filename,expected in json.loads((model/(name+'.json')).read_bytes()).items():
            require(digest(safe(model/name,filename))==expected,'Preserved code differs')
    require(digest(model/'implementation/intake.py')==body['extractor_sha256'],'Extractor differs from data receipt')
    recipe=json.loads((model/'recipe.json').read_bytes())
    require(recipe['blueprint_sha256']==report['blueprint_sha256'],'Training uses another Blueprint')
    checkpoints={};tensor_files=0
    for record in records:
        if record['kind']!='checkpoint':continue
        payload=record['payload'];path=safe(model,payload['path'])
        require(digest(path)==payload['sha256'],'Checkpoint descriptor differs')
        desc=json.loads(path.read_bytes());step=desc['step']
        require(desc['config_sha256']==initial['config_sha256'] and desc['recipe_sha256']==initial['recipe_sha256'],'Checkpoint inputs differ')
        require(path==model.resolve()/'checkpoints'/f'{step:07d}'/'checkpoint.json','Unexpected checkpoint path')
        if not metadata_only:
            require(digest(path.parent/'state.safetensors')==desc['state_sha256'],'Tensor artifact differs');tensor_files+=1
        checkpoints[step]=desc
    summary=json.loads((model/'summary.json').read_bytes())
    require(summary==report['summary'],'Summary anchor differs')
    require(any(r['kind']=='training-result' and r['payload']['sha256']==digest(model/'summary.json') for r in records),'Unsigned summary')
    require(summary['selected_step'] in checkpoints,'Selected checkpoint absent')
    require(summary['blueprint_sha256']==report['blueprint_sha256'] and summary['planned_steps']==recipe['max_steps'],'Training summary inputs differ')
    require(0<summary['completed_steps']<=recipe['max_steps'] and summary['completed_steps'] in checkpoints,'Training boundary absent')
    require(checkpoints[summary['selected_step']]['evaluation']['mean_domain_nll']==summary['validation_mean_nll'],'Selection score differs')
    require(summary['validation_mean_nll']==min(d['evaluation']['mean_domain_nll'] for d in checkpoints.values() if d.get('evaluation')),'Selected checkpoint is not validation minimum')
    replays=[]
    for record in records:
        if record['kind']!='replay':continue
        payload=record['payload'];start=payload['start_step'];end=payload['end_step']
        path=model/f'replay-{start:07d}-{end:07d}.json'
        require(digest(path)==payload['sha256'],'Replay receipt differs')
        replay=json.loads(path.read_bytes())
        require(replay['start_step']==start and replay['end_step']==end,'Replay payload boundaries differ')
        require(start in checkpoints and end in checkpoints and 0<=start<end<=summary['completed_steps'],'Replay boundary differs')
        require(replay['accepted'] is True and replay['optimizer_exact'] is True and replay['max_absolute']==0,'Replay rejected')
        require(replay['tensor_count']>0 and replay['exact_tensors']==replay['tensor_count']==len(replay['checks']) and all(c['exact'] and c['max_absolute']==0 for c in replay['checks']),'Tensor replay checks differ')
        replays.append({'start':start,'end':end,'tensors':replay['tensor_count']})
    require(any(r['start']==0 for r in replays),'Initialization replay missing')
    require(any(r['end']==summary['selected_step'] for r in replays),'Selected-checkpoint replay missing')
    if report.get('comparison_sha256'):
        require(digest(root/'comparison.json')==report['comparison_sha256'],'Comparison anchor differs')
        comparison=json.loads((root/'comparison.json').read_bytes())
        implementation=(root/'comparison-implementation.py') if metadata_only else Path(__file__).parent/'compare_seed_models.py'
        require(digest(implementation)==comparison['implementation_sha256'],'Comparison implementation differs')
        seal=verify_signature(json.loads((root/'comparison-receipt.json').read_bytes()),public)
        require(seal['comparison_sha256']==report['comparison_sha256'] and seal['blueprint_sha256']==report['blueprint_sha256'],'Comparison signature binding differs')
        require(comparison['blueprint_sha256']==report['blueprint_sha256'] and comparison['evaluation_sha256']==bp['evaluation_sha256'],'Comparison windows differ')
        require(comparison['models']['seed2_dense']['checkpoint_sha256']==checkpoints[summary['selected_step']]['state_sha256'],'Comparison uses another selected model')
    if report.get('paragraph_audit_sha256'):
        audit=root/('paragraph-audit.json' if metadata_only else 'paragraph-audit-final.json')
        require(digest(audit)==report['paragraph_audit_sha256'],'Paragraph audit differs')
        audit_body=json.loads(audit.read_bytes())
        require(audit_body['blueprint_sha256']==report['blueprint_sha256'],'Paragraph audit uses another Blueprint')
        implementation=(root/'paragraph-audit-implementation.py') if metadata_only else Path(__file__).parent/'audit_seed_overlap.py'
        require(digest(implementation)==audit_body['implementation_sha256'],'Paragraph auditor differs')
        require(audit_body['cross_split_document_pairs']==0,'Cross-split paragraph overlap found')
    return {'accepted':True,'metadata_only':metadata_only,'signed_records':len(records),'checkpoint_descriptors':len(checkpoints),
            'tensor_files_hashed':tensor_files,'data_receipt_signature':True,'replay_intervals':replays,
            'blueprint_sha256':report['blueprint_sha256'],
            'scope':'Signature and artifact integrity checks against supplied public anchors; numerical replay is the recorded work, not rerun by this checker.'}


def verify_release(root,expected_public_key):
    root=Path(root);report=json.loads((root/'report.json').read_bytes())
    require(report['operator_public_key']==expected_public_key,'Release signer differs from trusted key')
    seal=verify_signature(json.loads((root/'release-attestation.json').read_bytes()),expected_public_key)
    for name,field in [('report.json','report_sha256'),('artifact-verification.json','artifact_verification_sha256'),('sha256-manifest.json','manifest_sha256')]:
        require(digest(root/name)==seal[field],'Release seal differs: '+name)
    manifest=json.loads((root/'sha256-manifest.json').read_bytes())
    required={'report.json','artifact-verification.json','comparison.json','comparison-receipt.json','README.md','learning.png','learning.pdf'}
    require(required<=set(manifest),'Incomplete release manifest')
    for name,expected in manifest.items():require(digest(safe(root,name))==expected,'Release file changed: '+name)
    checked=verify(root,report,metadata_only=True)
    recorded=json.loads((root/'artifact-verification.json').read_bytes())
    require(recorded['accepted'] is True and recorded['metadata_only'] is False,'Full artifact verification absent')
    require(recorded['blueprint_sha256']==checked['blueprint_sha256'] and recorded['replay_intervals']==checked['replay_intervals'],'Artifact receipt inputs differ')
    require(recorded['tensor_files_hashed']==checked['checkpoint_descriptors'],'Incomplete tensor artifact coverage')
    return {**checked,'release_files_checked':len(manifest),'release_signer':expected_public_key}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--report',type=Path);p.add_argument('--metadata-only',action='store_true');p.add_argument('--expected-public-key');a=p.parse_args()
    if a.expected_public_key:result=verify_release(a.root,a.expected_public_key)
    elif a.report:result=verify(a.root,json.loads(a.report.read_bytes()),a.metadata_only)
    else:p.error('Supply --expected-public-key for a sealed release, or --report for an artifact check')
    print(json.dumps(result))
