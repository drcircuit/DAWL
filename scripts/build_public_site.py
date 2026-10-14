"""Allowlisted public website build. Never reads secrets or participant stores."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import verify_signature

PUBLIC_KEY='3eedb69bceac39ca67320c3a6d704fe12dc4462cac7a56171ab804f2bc3ec537'

def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()

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
        for kind,filename,field in [('training-result','training-summary.json','summary_sha256'),('serving-package','serving-package.json','sha256')]:
            if body['kind']==kind and hashfile(base/filename)!=body['payload'][field]:raise ValueError('Signed metadata changed')
    checks['signed_lineage']={'records':len(rows),'head':previous,'public_key':PUBLIC_KEY}
    diagnostic=ROOT/'docs/results/finetune-repeatability-001/evidence'
    attestation=verify_signature(json.loads((diagnostic/'attestation.json').read_text()),PUBLIC_KEY)
    if attestation['result_sha256']!=hashfile(diagnostic/'replay-verification.json') or attestation['source_lineage_head']!=previous:raise ValueError('Diagnostic signature mismatch')
    checks['diagnostic_signature_verified']=True
    for name in ('index.html','style.css','app.js','404.html'):copy(ROOT/'site'/name,name)
    for name in ('research-handoff-2026-10-14.md','trust-study-v2.md'):copy(ROOT/'docs'/name,'reports/'+name)
    copy(ROOT/'output/pdf/Hypertrix_Yngvar_2026-10-14.pdf','reports/Hypertrix_Yngvar_2026-10-14.pdf')
    for experiment,name in [('finetune-economics-001','measured-results.png'),('finetune-repeatability-001','repeatability.png')]:copy(ROOT/'docs/results'/experiment/name,'figures/'+name)
    checkpath=output/'reports/publication-checks.json';checkpath.write_text(json.dumps(checks,indent=2),encoding='utf-8');files['reports/publication-checks.json']=hashfile(checkpath)
    (output/'manifest.json').write_text(json.dumps({'schema':'hypertrix.public-site/1','files':files},indent=2),encoding='utf-8')
    print(json.dumps({'files':len(files),'output':str(output),'signed_records':len(rows)}))
    return output

if __name__=='__main__':build()
