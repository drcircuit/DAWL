"""Export a checked Seed II release, excluding source text, weights and private keys."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from dawl_node.common import sign,load_key,write_json
from verify_seed_release import digest,verify


def report(root,intake,output):
    root=Path(root);output=Path(output);model=root/'dense';dataset=root/'dataset'
    summary=json.loads((model/'summary.json').read_bytes());bp=json.loads((dataset/'blueprint.json').read_bytes())
    sources=json.loads((dataset/'sources.json').read_bytes())
    counts=Counter((d['split'],d['domain']) for d in sources['documents'])
    result={'schema':'hypertrix.seed-report/2','blueprint_sha256':digest(dataset/'blueprint.json'),'summary':summary,
        'training_targets_per_epoch':bp['arrays']['train']['targets'],'documents':bp['documents'],'norwegian_documents':bp['domains']['norwegian'],
        'operator_public_key':(model/'operator.public.key').read_text().strip(),'lineage_sha256':digest(model/'lineage.jsonl'),
        'data_verification':json.loads((dataset/'data-verification.json').read_bytes()),
        'data_verifier_public_key':(dataset/'verifier.public.key').read_text().strip(),
        'source_counts':{'/'.join(k):v for k,v in counts.items()},
        'scope':'From-scratch single-GPU training; pinned-runtime separate-process interval replay. No multi-host training or Oracle certification claimed.'}
    output.mkdir(parents=True,exist_ok=True)
    def copy(source,name):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for name in ('blueprint.json','sources.json','windows.json','tokenizer.json','evaluation.json','group-policy.json','data-verification.json','verifier.public.key','verifier-implementation.py'):
        copy(dataset/name,'dataset/'+name)
    for name in ('source-lock.json','downloads.json'):copy(Path(intake)/name,'intake/'+name)
    # The native root keeps identical metadata copies for the full binary checker.
    (root/'intake').mkdir(exist_ok=True)
    for name in ('source-lock.json','downloads.json'):shutil.copyfile(Path(intake)/name,root/'intake'/name)
    for item in json.loads((Path(intake)/'downloads.json').read_bytes()):
        name=item['file']
        if name=='bokselskap.tar.gz':continue
        if Path(name).name!=name or not name.endswith('.html'):raise ValueError('Unexpected catalogue snapshot path')
        relative='intake/catalogue-snapshots/'+name+'.txt'
        copy(Path(intake)/name,relative)
        native=root/relative;native.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(intake)/name,native)
    for name in ('lineage.jsonl','recipe.json','config.json','implementation.json','dependencies.json','operator.public.key','baseline.json','summary.json'):
        copy(model/name,'dense/'+name)
    for dirname in ('implementation','dependencies'):
        for path in (model/dirname).rglob('*.py'):copy(path,'dense/'+str(path.relative_to(model)).replace('\\','/'))
    for path in (model/'checkpoints').glob('*/checkpoint.json'):copy(path,'dense/'+str(path.relative_to(model)).replace('\\','/'))
    for path in model.glob('replay-*.json'):copy(path,'dense/'+path.name)
    for label in ('benchmark','benchmark-flash'):
        if (root/label/'results.json').exists():copy(root/label/'results.json',label+'.json')
    copy(root/'dataset-first-body/blueprint.json','benchmark-blueprint.json')
    copy(root/'probe/replay-0000000-0000030.json','calibration-replay.json')
    for name in ('comparison.json','comparison-receipt.json'):copy(root/name,name)
    copy(Path(__file__).parent/'compare_seed_models.py','comparison-implementation.py')
    result['comparison_sha256']=digest(root/'comparison.json')
    if (root/'paragraph-audit-final.json').exists():
        copy(root/'paragraph-audit-final.json','paragraph-audit.json')
        result['paragraph_audit_sha256']=digest(root/'paragraph-audit-final.json')
        copy(Path(__file__).parent/'audit_seed_overlap.py','paragraph-audit-implementation.py')
    verification=verify(root,result)
    result['replay_intervals']=verification['replay_intervals']
    result['replay_tensor_count']=min(r['tensors'] for r in verification['replay_intervals'])
    write_json(output/'report.json',result)
    write_json(output/'artifact-verification.json',verification)
    verify(output,result,metadata_only=True)
    attestation=sign({'schema':'hypertrix.seed-release/1','report_sha256':digest(output/'report.json'),
                      'artifact_verification_sha256':digest(output/'artifact-verification.json')},load_key(model/'operator.private.pem'))
    write_json(output/'release-attestation.json',attestation)
    print(json.dumps({'selected_step':summary['selected_step'],'status':summary['status'],**verification}),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--intake',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report(a.root,a.intake,a.output)
