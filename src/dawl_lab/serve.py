"""Checkpoint studio: CPU inference remains independent of the training worker."""
import argparse
import json
from pathlib import Path
import threading
import time
import torch
from tokenizers import Tokenizer
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field
from dawl_scratch.data import file_hash
from .run import configure,load_checkpoint,validate_lineage


class Completion(BaseModel):
    prompt:str=Field(min_length=1,max_length=12000)
    max_tokens:int=Field(default=192,ge=1,le=512)
    temperature:float=Field(default=.7,ge=0,le=1.5)
    top_p:float=Field(default=.9,gt=0,le=1)
    repetition_penalty:float=Field(default=1.1,ge=1,le=1.5)
    seed:int=Field(default=42,ge=0,le=2**31-1)


@torch.inference_mode()
def generate(model,tokenizer,bp,body):
    ids=tokenizer.encode(body.prompt,add_special_tokens=False).ids
    if not ids:raise ValueError('No prompt tokens')
    context=model.config.context;tokens=ids[-context:];cache=None;generated=[]
    rng=torch.Generator(device='cpu').manual_seed(body.seed)
    began=time.monotonic();reason='length';model.eval()
    for _ in range(body.max_tokens):
        if cache is not None and cache[0][0].shape[-2]>=context:cache=None
        inp=tokens[-context:] if cache is None else tokens[-1:]
        scores,_,cache=model(torch.tensor([inp],dtype=torch.long),cache=cache,use_cache=True,only_last=True)
        logits=scores[0,-1].float()
        for token in set(generated[-64:]):
            logits[token]=logits[token]/body.repetition_penalty if logits[token]>=0 else logits[token]*body.repetition_penalty
        logits[bp['pad_id']]=float('-inf');logits[tokenizer.token_to_id('<|bos|>')]=float('-inf')
        if body.temperature==0:chosen=int(logits.argmax())
        else:
            scores,indices=torch.sort(logits/body.temperature,descending=True)
            probability=scores.softmax(-1);remove=probability.cumsum(-1)-probability>=body.top_p
            probability[remove]=0;probability/=probability.sum()
            chosen=int(indices[torch.multinomial(probability,1,generator=rng)])
        if chosen==bp['eos_id']:reason='end-of-document';break
        generated.append(chosen);tokens.append(chosen)
    return {'text':tokenizer.decode(generated,skip_special_tokens=True),'tokens':len(generated),'prompt_tokens':len(ids),
            'context':context,'prompt_truncated':len(ids)>context,'elapsed_seconds':time.monotonic()-began,'finish_reason':reason}


def application(root,dataset,html):
    configure('cpu','fp32','math',4);api=FastAPI(docs_url=None,redoc_url=None,openapi_url=None);lock=threading.Lock();cached={}
    def status():
        if not (root/'recipe.json').exists():
            current=json.loads((dataset/'progress.json').read_bytes()) if (dataset/'progress.json').exists() else {}
            return {'ready':False,'phase':current.get('phase','Preparing the experiment'),'progress':current}
        records=validate_lineage(root);checkpoints=[r['payload'] for r in records if r['kind']=='checkpoint' and r['payload'].get('evaluation')]
        if not checkpoints:return {'ready':False,'phase':'Training the first checkpoint','progress':json.loads((root/'progress.json').read_bytes()) if (root/'progress.json').exists() else {}}
        chosen=min(checkpoints,key=lambda c:c['evaluation']['weighted_nll'])
        recipe=json.loads((root/'recipe.json').read_bytes());bp=json.loads((dataset/'blueprint.json').read_bytes())
        if file_hash(dataset/'blueprint.json')!=recipe['blueprint_sha256'] or file_hash(dataset/'tokenizer.json')!=bp['tokenizer_sha256']:raise ValueError('Dataset identity differs')
        summary=json.loads((root/'summary.json').read_bytes()) if (root/'summary.json').exists() else None
        return {'ready':True,'config':json.loads((root/'config.json').read_bytes()),'selected':chosen,
                'curve':[{'step':c['step'],'nll':c['evaluation']['weighted_nll']} for c in checkpoints],
                'blueprint':{'sha256':recipe['blueprint_sha256'],'documents':bp['documents'],'domains':bp['domains'],'mixture':bp['mixture'],'arrays':bp['arrays']},
                'summary':summary,'progress':json.loads((root/'progress.json').read_bytes()) if (root/'progress.json').exists() else {},
                'replays':[r['payload'] for r in records if r['kind']=='replay']}
    @api.middleware('http')
    async def headers(request:Request,call_next):
        origin=request.headers.get('origin')
        if request.method=='POST' and origin and origin!=str(request.base_url).rstrip('/'):
            return HTMLResponse('Origin rejected',status_code=403)
        response=await call_next(request);response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response
    @api.get('/',response_class=HTMLResponse)
    def index():return html.read_text(encoding='utf-8')
    @api.get('/api/status')
    def listing():return status()
    @api.post('/api/complete')
    def complete(body:Completion):
        info=status()
        if not info['ready']:raise HTTPException(409,'First checkpoint is still training')
        if not lock.acquire(blocking=False):raise HTTPException(429,'A completion is already running')
        try:
            selected=info['selected'];identity=selected['state_sha256']
            if cached.get('identity')!=identity:
                cached.clear();model,_,_=load_checkpoint(root,selected['step'],'cpu')
                cached.update(identity=identity,model=model,tokenizer=Tokenizer.from_file(str(dataset/'tokenizer.json')),
                              blueprint=json.loads((dataset/'blueprint.json').read_bytes()))
            result=generate(cached['model'],cached['tokenizer'],cached['blueprint'],body)
            return {**result,'checkpoint':identity,'step':selected['step']}
        finally:lock.release()
    return api


if __name__=='__main__':
    import uvicorn
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--html',type=Path,default=Path('/work/lab/studio.html'));p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=8887)
    a=p.parse_args();uvicorn.run(application(a.root,a.dataset,a.html),host=a.bind,port=a.port,access_log=False)
