"""Allowlisted public website build. Never reads secrets or participant stores."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import sys
import subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import verify_signature

PUBLIC_KEY='3eedb69bceac39ca67320c3a6d704fe12dc4462cac7a56171ab804f2bc3ec537'
STRICT_PUBLIC_KEY='a30bc71e898549c4f43320fe30c6c3ad90779c87fa39ed490fb4ef2349ba8eb5'

def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def strict_release_checks(evidence):
    release=json.loads((evidence/'release.json').read_text())
    attestation=verify_signature(json.loads((evidence/'release-attestation.json').read_text()),STRICT_PUBLIC_KEY)
    if release['public_key']!=STRICT_PUBLIC_KEY or attestation['result_sha256']!=hashfile(evidence/'release.json'):
        raise ValueError('Strict release signature binding differs')
    checked=0
    for name,meta in release['files'].items():
        path=(evidence/name).resolve()
        if not path.is_relative_to(evidence.resolve()):raise ValueError('Unsafe release path')
        if path.is_file():
            if hashfile(path)!=meta['sha256'] or path.stat().st_size!=meta['bytes']:raise ValueError('Strict release metadata changed')
            checked+=1
        elif not name.startswith(('exports/','checkpoints/','replay-checkpoints/')):
            raise ValueError('Missing required strict release metadata')
    previous=None
    rows=[json.loads(line) for line in (evidence/'lineage.jsonl').read_text().splitlines() if line]
    for i,row in enumerate(rows):
        body=verify_signature(row,STRICT_PUBLIC_KEY)
        if body['sequence']!=i or body['previous']!=previous:raise ValueError('Strict lineage changed')
        previous=row['id']
    if previous!=release['lineage_head'] or previous!=attestation['lineage_head']:raise ValueError('Strict lineage head differs')
    return {'public_key':STRICT_PUBLIC_KEY,'release_sha256':hashfile(evidence/'release.json'),
            'available_bound_files_checked':checked,'signed_records':len(rows),'replay_status':release['replay_status'],
            'scope':'Signed release and available metadata checked here; binary and numerical checks are preserved in the signed release receipts.'}


def build(output=None):
    output=Path(output or ROOT/'output/public-site');output.mkdir(parents=True,exist_ok=True)
    files={}
    def copy(source,name):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);files[name]=hashfile(target)
    checks={'schema':'hypertrix.public-evidence-gate/1','scope':'Metadata checks at site build. Full binary and numerical checks remain the recorded experiments.','archives':{}}
    for experiment in ('finetune-economics-001','finetune-repeatability-001'):
        base=ROOT/'docs/results'/experiment
        manifest=json.loads((base/'sha256-manifest.json').read_text())
        for name,expected in manifest.items():
            path=(base/name).resolve()
            if not path.is_relative_to(base.resolve()) or hashfile(path)!=expected:raise ValueError('Archive hash mismatch: '+name)
        checks['archives'][experiment]={'manifest_files_checked':len(manifest)}
        copy(ROOT/'docs/results'/(experiment+'.zip'),'reports/'+experiment+'.zip')
    base=ROOT/'docs/results/finetune-economics-001/evidence'
    rows=[json.loads(line) for line in (base/'lineage.jsonl').read_text().splitlines() if line];previous=None
    for i,row in enumerate(rows):
        body=verify_signature(row,PUBLIC_KEY)
        if body['sequence']!=i or body['previous']!=previous:raise ValueError('Broken signed lineage')
        previous=row['id']
        if body['kind']=='experiment-inputs':
            for filename,field in [('dataset-lock.json','dataset_lock_sha256'),('recipe.json','recipe_sha256'),('probes-lock.json','probes_sha256')]:
                if hashfile(base/filename)!=body['payload'][field]:raise ValueError('Signed input metadata changed')
        for kind,filename,field in [('training-result','training-summary.json','summary_sha256'),('serving-package','serving-package.json','sha256')]:
            if body['kind']==kind and hashfile(base/filename)!=body['payload'][field]:raise ValueError('Signed metadata changed')
    checks['signed_lineage']={'records':len(rows),'head':previous,'public_key':PUBLIC_KEY}
    dataset=json.loads((base/'dataset-lock.json').read_text())
    for filename,field in [('source-lock.json','source_lock_sha256'),('base-lock.json','base_lock_sha256')]:
        if hashfile(base/filename)!=dataset[field]:raise ValueError('Dataset source binding changed')
    copy(base/'source-lock.json','reports/source-lock.json')
    diagnostic=ROOT/'docs/results/finetune-repeatability-001/evidence'
    attestation=verify_signature(json.loads((diagnostic/'attestation.json').read_text()),PUBLIC_KEY)
    if attestation['result_sha256']!=hashfile(diagnostic/'replay-verification.json') or attestation['source_lineage_head']!=previous:raise ValueError('Diagnostic signature mismatch')
    checks['diagnostic_signature_verified']=True
    strict=ROOT/'docs/results/finetune-strict-002'
    if strict.is_dir():
        for name,expected in json.loads((strict/'sha256-manifest.json').read_text()).items():
            path=(strict/name).resolve()
            if not path.is_relative_to(strict.resolve()) or hashfile(path)!=expected:raise ValueError('Strict archive changed')
        checks['strict_release']=strict_release_checks(strict/'evidence')
        copy(strict.with_suffix('.zip'),'reports/finetune-strict-002.zip')
        copy(strict/'README.md','reports/finetune-strict-002.md')
        copy(strict/'strict-results.png','figures/strict-results.png')
    for name in ('index.html','style.css','app.js','404.html','roadmap.json'):copy(ROOT/'site'/name,name)
    for name in ('research-handoff-2026-10-14.md',):copy(ROOT/'docs'/name,'reports/'+name)
    for name in ('oracle_training.md','training_verification.md','public_observatory.md'):copy(ROOT/'docs'/name,'reports/'+name)
    oracle=ROOT/'docs/results/oracle-training-run-003'
    from public_snapshot import wsl_path, GENESIS_PIN
    checked=subprocess.run(['wsl','-d','kali-linux','-u','root','--exec',wsl_path(ROOT/'artifacts/check-certificate'),
        '--genesis',wsl_path(oracle/'genesis.json'),'--genesis-sha256',GENESIS_PIN,
        '--certificate',wsl_path(oracle/'training-certificate.json')],capture_output=True,text=True,timeout=40)
    if checked.returncode:raise ValueError('Archived Oracle certificate rejected')
    checks['oracle_certificate']=json.loads(checked.stdout)
    oracle_zip=output/'reports/oracle-training-run-003.zip'
    with zipfile.ZipFile(oracle_zip,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(oracle.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(oracle.parent))
    files['reports/oracle-training-run-003.zip']=hashfile(oracle_zip)
    copy(oracle/'README.md','reports/oracle-training-run-003.md')
    split=ROOT/'docs/results/public-observatory-001'
    for name,expected in json.loads((split/'sha256-manifest.json').read_text()).items():
        path=(split/name).resolve()
        if not path.is_relative_to(split.resolve()) or hashfile(path)!=expected:raise ValueError('Split evidence changed')
    split_zip=output/'reports/public-observatory-001.zip'
    with zipfile.ZipFile(split_zip,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(split.iterdir()):
            if path.is_file():archive.write(path,Path(split.name)/path.name)
    files['reports/public-observatory-001.zip']=hashfile(split_zip)
    copy(ROOT/'output/pdf/Hypertrix_Yngvar_2026-10-14.pdf','reports/Hypertrix_Yngvar_2026-10-14.pdf')
    for experiment,name in [('finetune-economics-001','measured-results.png'),('finetune-repeatability-001','repeatability.png')]:copy(ROOT/'docs/results'/experiment/name,'figures/'+name)
    # Content-version the frontend assets so an existing open console gets the new UI.
    index=output/'index.html';html=index.read_text(encoding='utf-8')
    html=html.replace('href="style.css"','href="style.css?v='+files['style.css'][:12]+'"')
    html=html.replace('src="app.js"','src="app.js?v='+files['app.js'][:12]+'"')
    index.write_text(html,encoding='utf-8');files['index.html']=hashfile(index)
    checkpath=output/'reports/publication-checks.json';checkpath.write_text(json.dumps(checks,indent=2),encoding='utf-8');files['reports/publication-checks.json']=hashfile(checkpath)
    (output/'manifest.json').write_text(json.dumps({'schema':'hypertrix.public-site/1','files':files},indent=2),encoding='utf-8')
    print(json.dumps({'files':len(files),'output':str(output),'signed_records':len(rows)}))
    return output

if __name__=='__main__':build()
