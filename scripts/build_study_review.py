"""Build only public review stimuli and signed evidence; never read participant stores."""
import hashlib
import json
from pathlib import Path
import tempfile
from build_public_site import strict_release_checks
from trust_study import Study, encoded

REPO=Path(__file__).resolve().parents[1]


def build(output=None):
    output=Path(output or REPO/'output/study-review');output.mkdir(parents=True,exist_ok=True)
    source=REPO/'site/study';evidence=REPO/'docs/results/finetune-strict-002/evidence'
    config=REPO/'research/trust-study-model-strict-002.json'
    checked=strict_release_checks(evidence)
    model=json.loads(config.read_text(encoding='utf-8'))
    if checked['release_sha256']!=model['release_sha256']:raise ValueError('Study documentation release differs')
    files={}
    def put(name,raw):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        files[name]=hashlib.sha256(raw).hexdigest()
    for name in ('trust_study.css','study-advice.js','study-provenance.js'):
        put('assets/'+name,(REPO/'scripts'/name).read_bytes())
    for name in ('review.css','review-index.js','review-participant.js'):
        put('assets/'+name,(source/name).read_bytes())
    for name in ('index.html','participant.html'):
        put(name,(source/name).read_bytes())
    put('preview.html',(source/'participant.html').read_bytes())
    put('404.html','<!doctype html><html lang="nb"><meta charset="utf-8"><title>Ukjent side</title><h1>Siden finnes ikke</h1><a href="/">Til forskeroversikten</a></html>'.encode())
    put('robots.txt',b'User-agent: *\nDisallow: /\n')
    interface=[source/name for name in ('index.html','participant.html','review.css','review-index.js','review-participant.js')]
    interface += [REPO/'scripts'/name for name in ('trust_study.css','study-advice.js','study-provenance.js')]
    with tempfile.TemporaryDirectory(prefix='hypertrix-study-review-') as tmp:
        study=Study(tmp,REPO/'research/trust-study-v2.json',interface,evidence,config)
        trials={}
        if len(study.evidence)!=5:raise ValueError('All five study evidence documents required')
        for filename,digest in study.evidence.items():put('evidence/'+digest+'.json',(evidence/filename).read_bytes())
        for step in study.docs['C']['steps']:
            step['evidence']='evidence/'+step['evidence'].rsplit('/',1)[-1]+'.json'
        study.version=hashlib.sha256(encoded(study.snapshot).encode()).hexdigest()
        for case in study.cases:
            for variant in ('good','bad'):
                for condition in 'ABC':
                    trial=json.loads(encoded(study.payload(case,variant,condition)))
                    trials[f'{case}/{variant}/{condition}']=trial
        data={'schema':'hypertrix.study-review/1','mode':'review-only','responses_collected':False,
              'model_release':model['model_release'],'release_sha256':model['release_sha256'],
              'cases':[{'id':c['id'],'title':c['title']} for c in study.cases.values()],'trials':trials}
        put('data/review.json',encoded(data).encode())
    put('evidence/publication-checks.json',encoded(checked).encode())
    # Only this allowlist can be deployed. Temporary SQLite never enters output.
    manifest={'schema':'hypertrix.study-review-files/1','mode':'review-only','files':files}
    (output/'manifest.json').write_bytes(encoded(manifest).encode())
    print(json.dumps({'files':len(files),'review_variants':len(trials),'responses_collected':False}))
    return manifest


if __name__=='__main__':build()
