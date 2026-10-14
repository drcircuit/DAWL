"""Local comparison and provenance-reveal lab using real model responses.

Bound to loopback. No mocked answers, financial transactions, or participant
recruitment. Pilot measurements remain exploratory until expert review/design.
"""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys
import time
import uuid
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Literal
from trust_study import Study
import uvicorn
from trust_lab_secrets import load_key, save_key
from trust_provenance import participant_view

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from dawl_node.common import canonical_json, sha256, write_json

p=argparse.ArgumentParser();p.add_argument('--pilot',type=Path,required=True)
p.add_argument('--root',type=Path,default=REPO/'artifacts/trust-lab-001');p.add_argument('--port',type=int,default=8885)
args=p.parse_args();args.root.mkdir(parents=True,exist_ok=True)
CASES=json.loads((REPO/'research/finance-trust-cases-v1.json').read_text(encoding='utf-8'))
CASE_MAP={c['id']:c for c in CASES['cases']}
KEY_FILE=REPO/'secrets/trust-lab-openai.dpapi';TOKEN=secrets.token_urlsafe(32)
OPENAI_MODEL='gpt-5.4-mini-2026-03-17';LIMIT_USD=.25
lock=asyncio.Lock();sessions={}
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
PUBLIC_FILES=['source-lock.json','base-lock.json','dataset-lock.json','segments.jsonl','recipe.json','probes-lock.json',
              'gpu-session.json','lineage.jsonl','operator.public.key','baseline.json','selected-evaluation.json',
              'training-summary.json','control-result.json','control-codebook.json','exports.json','verification.json',
              'runtime-image-id.txt','runtime-image-002-id.txt','replay-verification.json','verifier-replay.py',
              'serving-package.json','merge-verification.json','merge-verification-single-round.json',
              'replay-initialization-diagnostic.json','replay-kernel-diagnostic.json','replay-checkpoint-1.json',
              'ollama-base.json','ollama-epoch-1.json','ollama-epoch-2.json','ollama-epoch-3.json']
REPEATABILITY_POLICIES=('legacy-auto','math-only','strict-auto','strict-math')
REPEATABILITY_FILES=['replay-verification.json','attestation.json','operator.public.key','gpu-session.json',
                     'finetune_repeatability.py','execution.py','runtime-image-id.txt']+[
                     name+'-comparison.json' for name in REPEATABILITY_POLICIES]


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default


def repeatability_summary():
    root=args.pilot.parent/'finetune-repeatability-001'
    rows=[]
    for name in REPEATABILITY_POLICIES:
        comparison=read(root/(name+'-comparison.json'))
        attempt=read(root/(name+'-1')/'result.json')
        if comparison and attempt:
            rows.append({'policy':name,'steps':attempt['steps'],'comparison':comparison,
                         'attention_operations':attempt['attention_operations']})
    final=read(root/'replay-verification.json')
    return {'started':(root/'gpu-session.json').exists(),'complete':final is not None,'comparisons':rows,
            'scope':'Separate short repeatability diagnostic. The original epoch replay remains a mismatch.',
            'links':[{'title':name,'url':'/evidence/repeatability/'+name} for name in REPEATABILITY_FILES if (root/name).is_file()]}


def strict_run_summary():
    root=args.pilot.parent/'finetune-strict-002'
    events=[]
    try:events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines() if line][-5:]
    except (OSError,ValueError):pass
    return {'started':(root/'gpu-session.json').is_file(),'session':read(root/'gpu-session.json'),
            'summary':read(root/'training-summary.json'),'replay':read(root/'replay-verification.json'),
            'progress':read(root/'replay-progress.json'),'events':events}


def now():return datetime.now(timezone.utc).isoformat()


def append(name,value):
    with (args.root/name).open('a',encoding='utf-8') as f:f.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')


def registry():
    return read(args.pilot/'ollama-models.json',{'models':[]})


def model_info(model_id):
    if model_id=='openai':return {'id':'openai','name':OPENAI_MODEL,'label':'OpenAI · GPT-5.4 mini','provider':'openai'}
    result=next((m for m in registry()['models'] if m['id']==model_id),None)
    if result is None:raise HTTPException(409,'This local checkpoint has not been exported yet.')
    return result


def prompt_for(case):
    return CASES['instruction']+'\n\nSituation: '+case['title']+'\nDate: '+case['as_of']+'\n\n'+case['facts']+'\n\n'+case['question']


def response_path(model_id,case_id):
    return args.root/'responses'/f'{model_id}--{case_id}.json'


def disclosure_record(sid, disclosure):
    digest=sha256(canonical_json(disclosure))
    append('disclosures.jsonl',{'session_id':sid,'utc':now(),'sha256':digest,'content':disclosure,'demo_only':True})
    sessions[sid]['disclosure_sha256']=digest
    return disclosure


def spent():
    path=args.root/'api-budget.jsonl'
    if not path.exists():return 0.
    # Reserve worst-case cost BEFORE requests. Failed/uncertain requests retain their reservation.
    return sum(json.loads(line)['reserved_usd'] for line in path.read_text().splitlines() if line)


def provenance(model_id):
    if model_id=='openai':return {'title':'Provider documentation only','level':'Declared',
        'summary':'OpenAI documents model capabilities and its development approach. This lab has no access to the training corpus, weights or independently replayable training record.',
        'links':[{'title':'Official model documentation','url':'https://developers.openai.com/api/docs/models/gpt-5.4-mini'}],
        'boundaries':['API model identifier and response metadata are recorded.','No claim that the fine-tuning ledger covers OpenAI training.'],
        'participant':participant_view(model_id)}
    upstream=[{'title':'Qwen model card, license and published files','url':'https://huggingface.co/Qwen/Qwen3-0.6B/tree/c1899de289a04d12100db370d81485cdf75e47ca'},
              {'title':'Qwen3 technical report','url':'https://arxiv.org/abs/2505.09388'}]
    if model_id=='open':return {'title':'Open-weight provider documentation','level':'Declared',
        'summary':'Official Qwen3-0.6B weights, used unchanged. The provider publishes weights, a model card and a technical report; this does not provide complete item-level lineage of pretraining.',
        'links':upstream,'boundaries':['The experiment identifies the exact downloaded revision.','Provider documentation does not prove which training examples caused an answer.'],
        'participant':participant_view(model_id)}
    verification=read(args.pilot/'verification.json',{})
    replay=read(args.pilot/'replay-verification.json',{})
    participant=participant_view(model_id,bool(verification.get('passed')),replay.get('status')=='exact-match',
                     replay_status=replay.get('status'),answer_recorded=any(response_path(model_id,c['id']).exists() for c in CASES['cases']))
    participant['source_books']=[{'title':b['title'],'author':b.get('author',''),'domain':b['domain'],
                                 'url':b.get('catalogue_url',b['source_url'])}
                                for b in read(args.pilot/'source-lock.json',{'books':[]})['books']]
    return {'title':'Traceable local adaptation, with explicit verification scope',
        'level':'Artifact checks passed' if verification.get('passed') else 'Verification pending',
        'summary':'The fine-tuned model inherits Qwen\'s upstream provenance limits. The local continuation adds original source hashes, rights declarations, cleaning offsets, training/holdout splits, recipe, checkpoint hashes and signed lineage.',
        'links':upstream+[{'title':f,'url':'/evidence/'+f} for f in PUBLIC_FILES if (args.pilot/f).exists()],
        'verification':verification,'replay':replay,
        'participant':participant,
        'boundaries':['Local signatures identify the recorded operator; they are not an independent party.','This run is not certified by the Oracle validator ring.',
                      'Replay, where recorded, verifies a specified computation in a specified environment; it does not establish financial correctness.',
                      'No complete lineage of upstream Qwen pretraining is available. No proof links a particular book passage causally to an answer.']}


@app.middleware('http')
async def local_boundary(request:Request,call_next):
    if request.headers.get('host') not in (f'127.0.0.1:{args.port}',f'localhost:{args.port}'):
        return JSONResponse({'detail':'Local host only'},status_code=403)
    if request.method=='POST':
        if request.headers.get('x-lab-token')!=TOKEN:
            return JSONResponse({'detail':'Reload the local lab page'},status_code=403)
        origin=request.headers.get('origin')
        if origin and origin not in (f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'):
            return JSONResponse({'detail':'Same-origin requests only'},status_code=403)
        if int(request.headers.get('content-length','0'))>32768:
            return JSONResponse({'detail':'Request too large'},status_code=413)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    return response


@app.get('/')
async def index():
    return HTMLResponse((REPO/'scripts/trust_lab.html').read_text(encoding='utf-8').replace('__LAB_TOKEN__',TOKEN))


@app.get('/assets/{filename}')
async def asset(filename:str):
    if filename not in ('trust_provenance.js','trust_provenance.css','trust_study.js','trust_study.css','study-advice.js','study-provenance.js'):raise HTTPException(404)
    return FileResponse(REPO/'scripts'/filename)


@app.get('/api/status')
async def status():
    events=[]
    try:events=[json.loads(l) for l in (args.pilot/'events.jsonl').read_text().splitlines() if l][-300:]
    except (OSError,ValueError):pass
    models=registry()['models']+[model_info('openai')]
    for m in models:
        m['ready']=bool(load_key(KEY_FILE)) if m['id']=='openai' else True
    cached=[{'model_id':m['id'],'case_id':c['id']} for m in models for c in CASES['cases'] if response_path(m['id'],c['id']).exists()]
    return {'models':models,'cases':[{k:v for k,v in c.items() if k!='rubric'} for c in CASES['cases']],
            'events':events,'summary':read(args.pilot/'training-summary.json'),'control':read(args.pilot/'control-result.json'),
            'verification':read(args.pilot/'verification.json'),'replay':read(args.pilot/'replay-verification.json'),
            'replay_progress':read(args.pilot/'replay-progress.json'),
            'repeatability':repeatability_summary(),
            'strict_run':strict_run_summary(),
            'cached':cached,'api_reserved_usd':spent(),'api_limit_usd':LIMIT_USD,
            'study_scope':'Technical pilot. Comparative models differ in capability and training; that comparison does not isolate the effect of provenance.'}


@app.get('/evidence/{filename}')
async def evidence(filename:str):
    if filename not in PUBLIC_FILES or not (args.pilot/filename).is_file():raise HTTPException(404)
    return FileResponse(args.pilot/filename,filename=filename)


@app.get('/evidence/repeatability/{filename}')
async def repeatability_evidence(filename:str):
    path=args.pilot.parent/'finetune-repeatability-001'/filename
    if filename not in REPEATABILITY_FILES or not path.is_file():raise HTTPException(404)
    return FileResponse(path,filename=filename)


@app.get('/api/provenance/{model_id}')
async def evidence_description(model_id:str):
    # The development trail is inspectable while training/serving is pending.
    if model_id!='hypertrix':model_info(model_id)
    return provenance(model_id)


class Credential(BaseModel):key:str=Field(min_length=20,max_length=511)


@app.post('/api/credential')
async def credential(body:Credential):
    try:save_key(KEY_FILE,body.key.strip())
    except (ValueError,OSError,RuntimeError):raise HTTPException(400,'Could not protect the local credential')
    return {'stored':True,'protection':'Windows DPAPI, current user'}


async def infer(info,prompt,study=False):
    start=time.monotonic()
    async with httpx.AsyncClient(timeout=240,trust_env=False) as client:
        if info['provider']=='openai':
            key=load_key(KEY_FILE)
            if not key:raise HTTPException(409,'Connect an OpenAI API key in local settings first.')
            # UTF-8 byte length is a conservative upper bound for this text input's token count.
            reserve=(len(prompt.encode('utf-8'))+256)*.75/1e6+1600*4.5/1e6
            if spent()+reserve>LIMIT_USD:raise HTTPException(409,'This pilot has reached its USD 0.25 API reservation cap.')
            append('api-budget.jsonl',{'utc':now(),'reserved_usd':reserve,'model':OPENAI_MODEL})
            payload={'model':OPENAI_MODEL,'input':prompt,'store':False,'max_output_tokens':1600,'reasoning':{'effort':'none'}}
            response=await client.post('https://api.openai.com/v1/responses',headers={'Authorization':'Bearer '+key},json=payload)
            if response.status_code!=200:raise HTTPException(502,f'OpenAI returned HTTP {response.status_code}; no answer was fabricated.')
            result=response.json()
            text='\n'.join(c['text'] for o in result.get('output',[]) if o.get('type')=='message' for c in o.get('content',[]) if c.get('type')=='output_text')
            meta={'provider':'OpenAI','model':result.get('model'),'response_id':result.get('id'),'status':result.get('status'),
                  'usage':result.get('usage'),'parameters':{k:v for k,v in payload.items() if k!='input'}}
            if result.get('status')!='completed' or not text.strip():raise HTTPException(502,'OpenAI response was incomplete; no study answer saved.')
        else:
            options={'temperature':.7,'top_p':.8,'top_k':20,'seed':20261014,'num_predict':420,'num_ctx':4096}
            # Frozen case answers use the same CPU placement as the captured baseline.
            # Exploratory chat also stays off the GPU until the bounded replay finishes.
            if study or not (args.pilot/'replay-verification.json').exists():options['num_gpu']=0
            payload={'model':info['name'],'messages':[{'role':'user','content':prompt}],'think':False,'stream':False,'options':options,'keep_alive':0}
            response=await client.post('http://127.0.0.1:11434/api/chat',json=payload)
            if response.status_code!=200:raise HTTPException(502,f'Ollama returned HTTP {response.status_code}; no answer was fabricated.')
            result=response.json();text=result.get('message',{}).get('content','')
            meta={'provider':'Ollama local','model':result.get('model'),'parameters':options,'think':False,
                  'eval_count':result.get('eval_count'),'prompt_eval_count':result.get('prompt_eval_count'),
                  'done_reason':result.get('done_reason'),'ollama_digest':info.get('digest')}
            if not text.strip():raise HTTPException(502,'Ollama returned an empty answer.')
        return text,{**meta,'latency_seconds':time.monotonic()-start}


class Generate(BaseModel):model_id:str=Field(max_length=40);case_id:str=Field(max_length=60)


@app.get('/api/response/{model_id}/{case_id}')
async def saved_response(model_id:str,case_id:str):
    model_info(model_id)
    if case_id not in CASE_MAP:raise HTTPException(404,'Unknown case')
    value=read(response_path(model_id,case_id))
    if value is None:raise HTTPException(404,'No saved response for this case')
    return value


@app.post('/api/generate')
async def generate(body:Generate):
    info=model_info(body.model_id);case=CASE_MAP.get(body.case_id)
    if not case:raise HTTPException(404,'Unknown case')
    async with lock:
        path=response_path(body.model_id,body.case_id)
        if path.exists():return read(path)
        prompt=prompt_for(case);answer,meta=await infer(info,prompt,study=True)
        record={'schema':'hypertrix.real-model-response/1','id':uuid.uuid4().hex,'model_id':body.model_id,'case_id':body.case_id,
                'created_utc':now(),'prompt':prompt,'prompt_sha256':sha256(prompt.encode()),'answer':answer,
                'answer_sha256':sha256(answer.encode()),'case_sha256':sha256(canonical_json(case)),
                'metadata':meta,'quality_review':'Not yet assessed by a financial domain expert.'}
        write_json(path,record);return record


class Chat(BaseModel):model_id:str=Field(max_length=40);message:str=Field(min_length=1,max_length=6000)


@app.post('/api/chat')
async def chat(body:Chat):
    info=model_info(body.model_id)
    async with lock:
        answer,metadata=await infer(info,body.message)
        return {'answer':answer,'metadata':metadata,'scope':'Exploratory single-turn chat; not included in study response records.'}


class Trial(BaseModel):case_id:str;mode:str='reveal'


@app.post('/api/trial')
async def trial(body:Trial):
    if body.case_id not in CASE_MAP or body.mode not in ('reveal','controlled'):raise HTTPException(400)
    available=[read(response_path(m['id'],body.case_id)) for m in registry()['models']+[model_info('openai')]]
    available=[r for r in available if r and r['model_id'] in ('open','hypertrix','openai')]
    if body.mode=='controlled':
        if not read(args.pilot/'verification.json',{}).get('passed'):
            raise HTTPException(409,'Controlled provenance trials require completed artifact verification.')
        available=[r for r in available if r['model_id']=='hypertrix']
    if not available:raise HTTPException(409,'Generate the relevant case answers first.')
    response=secrets.choice(available);condition=secrets.choice(['A','B','C']) if body.mode=='controlled' else 'blind-then-reveal'
    sid=uuid.uuid4().hex
    sessions[sid]={'response':response,'condition':condition,'mode':body.mode,'phase':'before','created':time.monotonic(),
                   'disclosure_sha256':None}
    result={'session_id':sid,'answer':response['answer'],'answer_sha256':response['answer_sha256'],'case_id':body.case_id,
            'mode':body.mode,'condition':condition if body.mode=='controlled' else None}
    if body.mode=='controlled':
        if condition=='A':result['disclosure']={'title':'No training information shown','summary':'Assess the advice itself.'}
        elif condition=='B':result['disclosure']={'title':'Developer statement','summary':provenance('hypertrix')['summary'],
                    'boundaries':provenance('hypertrix')['boundaries'],
                    'participant':participant_view('hypertrix',declared_only=True)}
        else:result['disclosure']=provenance('hypertrix')
        disclosure_record(sid,result['disclosure'])
    append('trials.jsonl',{'session_id':sid,'utc':now(),'mode':body.mode,'condition':condition,'response_id':response['id'],
                         'answer_sha256':response['answer_sha256'],'disclosure_sha256':sessions[sid]['disclosure_sha256'],
                         'interface_files':{name:sha256((REPO/'scripts'/name).read_bytes()) for name in
                                            ('trust_lab.html','trust_provenance.js','trust_provenance.css','trust_provenance.py')},
                         'demo_only':True})
    return result


class Rating(BaseModel):
    session_id:str;trust:int=Field(ge=1,le=7);reliance:str;decision:str=Field(max_length=2000);confidence:int=Field(ge=1,le=7)
    responsibility:str;phase:str


@app.post('/api/rating')
async def rating(body:Rating):
    session=sessions.get(body.session_id)
    if not session or body.phase!=session['phase']:raise HTTPException(409,'Trial phase is no longer available')
    if body.reliance not in ('follow','check','reject') or body.responsibility not in ('provider','curator','bank','user','shared','unsure'):
        raise HTTPException(400,'Invalid choice')
    response=session['response']
    row={**body.model_dump(),'utc':now(),'mode':session['mode'],'condition':session['condition'],
         'response_id':response['id'],'answer_sha256':response['answer_sha256'],'model_id':response['model_id'],
         'disclosure_sha256':session['disclosure_sha256'],
         'case_id':response['case_id'],'elapsed_seconds':time.monotonic()-session['created'],'demo_only':True}
    append('ratings.jsonl',row)
    if body.phase=='before' and session['mode']=='reveal':
        session['phase']='after'
        shown=disclosure_record(body.session_id,provenance(response['model_id']))
        return {'phase':'after','provenance':shown,'model':model_info(response['model_id'])}
    session['phase']='complete'
    return {'phase':'complete','message':'Pilot rating saved. No real financial action was taken.'}


@app.get('/api/research-summary')
async def research_summary():
    path=args.root/'ratings.jsonl';rows=[json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
    paired=[]
    for sid in {r['session_id'] for r in rows}:
        before=next((r for r in rows if r['session_id']==sid and r['phase']=='before'),None)
        after=next((r for r in rows if r['session_id']==sid and r['phase']=='after'),None)
        if before and after:paired.append(after['trust']-before['trust'])
    return {'pilot_ratings':len(rows),'paired_reveal_trials':len(paired),'mean_trust_change':sum(paired)/len(paired) if paired else None,
            'expert_review':'Pending; no advice-quality or decision-quality score has been fabricated.',
            'limitation':'Demo ratings are not research findings. Repeated reveal can cause demand and order effects; controlled A/B/C keeps the identical answer.'}


study=Study(args.root.parent/'trust-study-002',REPO/'research/trust-study-v2.json',
            [REPO/'scripts'/f for f in ('trust_study.html','trust_study.js','trust_study.css','study-advice.js','study-provenance.js','trust_study.py')],args.pilot)


@app.get('/participant')
@app.get('/study-preview')
async def participant():
    return HTMLResponse((REPO/'scripts/trust_study.html').read_text(encoding='utf-8').replace('__LAB_TOKEN__',TOKEN))


@app.get('/study-admin')
async def study_admin():
    return HTMLResponse('''<!doctype html><html lang="nb"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Forskeroversikt · Hypertrix</title><link rel="stylesheet" href="/assets/trust_study.css"><main><span class="eyebrow">Hypertrix · Forskeroversikt</span>
    <h1>Faste råd, variert dokumentasjon</h1><div class="card"><p>Tre scenarioer fra Yngvar. Seks ordrette rådvarianter. Faglig vurdering og vanskelighetskalibrering gjenstår; dette er en pilot.</p>
    <p>Én dokumentasjonsbetingelse per deltaker: A uten ekstra dokumentasjon, B utviklingsbeskrivelse, C samme beskrivelse med kontrollerbare dokumenter. Rådene er forskerskrevne, ikke modellgenererte.</p>
    <p><a href="/participant">Åpne deltakerpiloten</a> · <a href="/">Tidligere modellforsøk og tekniske resultater</a></p></div>
    <div class="card"><h2>Forhåndsvis uten å lagre svar</h2><div id="previews"></div></div>
    <div class="card"><h2>Datainnsamling</h2><p id="counts"></p><button id="export">Last ned forskerdata (JSON)</button><p class="small">Kun lokal eksport. Ingen svar eller øktnøkler publiseres til Azure. Visningstider inkluderer pauser og er ikke et mål på aktiv lesetid.</p></div></main>
    <script>const token='''+json.dumps(TOKEN)+''';
    const cases=['extra-payment','fund-fees','refinancing'];let html='';for(const c of cases){html+='<p>'+c+': ';for(const q of ['good','bad'])for(const d of ['A','B','C'])html+=`<a href="/study-preview?case_id=${c}&variant=${q}&condition=${d}">${q} / ${d}</a> &nbsp; `;html+='</p>'}document.getElementById('previews').innerHTML=html;
    async function data(){const r=await fetch('/api/study/export',{headers:{'X-Lab-Token':token}});if(!r.ok)throw Error('Eksport feilet');return r.json()}
    data().then(d=>document.getElementById('counts').textContent=`${new Set(d.rows.map(r=>r.session_id)).size} økter · ${d.rows.filter(r=>r.response).length} lagrede vurderinger`);
    document.getElementById('export').onclick=async()=>{const blob=new Blob([JSON.stringify(await data(),null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='hypertrix-study-v2-export.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
    </script></html>''')


class StudySession(BaseModel):
    session_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    session_token:str=Field(min_length=32,max_length=100)


class StudyStart(BaseModel):
    consent:Literal[True]


class StudyResponse(BaseModel):
    decision:str=Field(max_length=20)
    trust:int=Field(strict=True,ge=1,le=7)
    confidence:int=Field(strict=True,ge=1,le=7)
    difficulty:int=Field(strict=True,ge=1,le=7)
    reliance:Literal['follow','check','reject']
    reason:str=Field(max_length=1500,default='')


class StudySubmit(StudySession):
    ordinal:int=Field(strict=True,ge=0,le=2)
    response:StudyResponse


class StudyEvent(StudySession):
    ordinal:int=Field(strict=True,ge=0,le=2)
    kind:Literal['documentation_open','evidence_open']


def study_call(method,*params):
    try:return method(*params)
    except PermissionError as e:raise HTTPException(403,str(e))
    except ValueError as e:raise HTTPException(409,str(e))


@app.post('/api/study/start')
async def study_start(body:StudyStart):return study.start()


@app.post('/api/study/current')
async def study_current(body:StudySession):return study_call(study.current,body.session_id,body.session_token)


@app.post('/api/study/submit')
async def study_submit(body:StudySubmit):return study_call(study.submit,body.session_id,body.session_token,body.ordinal,body.response.model_dump())


@app.post('/api/study/event')
async def study_event(body:StudyEvent):
    study_call(study.event,body.session_id,body.session_token,body.ordinal,body.kind)
    return {'recorded':True}


@app.get('/api/study/preview')
async def study_preview(case_id:Literal['extra-payment','fund-fees','refinancing']='extra-payment',
                        variant:Literal['good','bad']='good',condition:Literal['A','B','C']='C'):
    return study.payload(case_id,variant,condition)


@app.get('/api/study/export')
async def study_export(request:Request):
    if request.headers.get('x-lab-token')!=TOKEN:raise HTTPException(403,'Local researcher access required')
    return {'rows':study.export(),'current_protocol_sha256':study.version,'status':study.protocol['status']}


@app.get('/study-evidence/{digest}')
async def study_evidence(digest:str):
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise HTTPException(404)
    path=study.root/'evidence'/digest
    if not path.is_file():raise HTTPException(404)
    return FileResponse(path,media_type='application/json')


if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)
