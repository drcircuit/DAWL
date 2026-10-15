"""Loopback-only checkpoint playground; generated source is displayed, never executed."""
import argparse
import json
from pathlib import Path
import threading
import time
import torch
from tokenizers import Tokenizer
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from .data import file_hash
from .run import configure, load_checkpoint


class Completion(BaseModel):
    model: str
    prompt: str = Field(min_length=1,max_length=6000)
    max_tokens: int = Field(default=96,ge=1,le=256)
    temperature: float = Field(default=.7,ge=0,le=1.5)
    seed: int = Field(default=42,ge=0,le=2**31-1)


def application(base, dataset, html):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    configure('cpu')
    lock=threading.Lock();cache={}
    blueprint=json.loads((dataset/'blueprint.json').read_bytes())
    if file_hash(dataset/'tokenizer.json')!=blueprint['tokenizer_sha256']:raise ValueError('Tokenizer hash mismatch')
    tokenizer=Tokenizer.from_file(str(dataset/'tokenizer.json'))

    def models():
        output=[]
        for name in ('dense','moe'):
            root=base/name
            if not (root/'recipe.json').exists():continue
            recipe=json.loads((root/'recipe.json').read_bytes())
            if recipe['blueprint_sha256']!=file_hash(dataset/'blueprint.json'):continue
            checkpoints=sorted(int(p.name) for p in (root/'checkpoints').iterdir() if p.name.isdigit() and (p/'checkpoint.json').exists())
            if not checkpoints:continue
            summary=json.loads((root/'summary.json').read_bytes()) if (root/'summary.json').exists() else None
            step=summary['selected_step'] if summary else checkpoints[-1]
            config=json.loads((root/'config.json').read_bytes())
            desc=json.loads((root/'checkpoints'/f'{step:07d}'/'checkpoint.json').read_bytes())
            replays=[json.loads(p.read_bytes()) for p in root.glob('replay-*.json')]
            output.append({'id':name,'label':'Hypertrix Seed · '+('Dense' if name=='dense' else '4 experts · top 2'),
                'step':step,'config':config,'checkpoint_sha256':desc['state_sha256'],
                'validation':desc.get('evaluation'),'training_status':summary['status'] if summary else 'training',
                'parameters':summary['parameters'] if summary else None,
                'replays':[{k:r[k] for k in ('start_step','end_step','accepted','exact_tensors','tensor_count')} for r in replays]})
        return output

    @app.middleware('http')
    async def origin_check(request,call_next):
        if request.method=='POST':
            origin=request.headers.get('origin')
            if origin and origin not in ('http://127.0.0.1:8886','http://localhost:8886'):
                return HTMLResponse('Origin rejected',status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.get('/',response_class=HTMLResponse)
    def index():return html.read_text(encoding='utf-8')

    @app.get('/api/models')
    def listing():return {'models':models(),'corpus':{'documents':blueprint['documents'],'domains':blueprint['domains'],
        'training_targets_per_epoch':blueprint['arrays']['train']['targets'],'blueprint_sha256':file_hash(dataset/'blueprint.json')},
        'mode':'completion','serving_device':'CPU'}

    @app.post('/api/complete')
    def complete(body:Completion):
        available={m['id']:m for m in models()}
        if body.model not in available:raise HTTPException(404,'Checkpoint not available')
        if not lock.acquire(blocking=False):raise HTTPException(429,'A completion is already running')
        try:
            info=available[body.model];root=base/body.model
            key=(body.model,info['step'],info['checkpoint_sha256'])
            if cache.get('key')!=key:
                model,_,_=load_checkpoint(root,info['step'],'cpu');model.eval()
                cache.clear();cache.update(key=key,model=model)
            model=cache['model'];began=time.monotonic()
            ids=tokenizer.encode(body.prompt,add_special_tokens=False).ids
            if not ids:raise HTTPException(400,'Prompt produced no tokens')
            prompt_tokens=len(ids);context=model.config.context
            tokens=torch.tensor([ids[-context:]],dtype=torch.long)
            generator=torch.Generator(device='cpu').manual_seed(body.seed)
            generated=[];reason='length'
            with torch.inference_mode():
                for _ in range(body.max_tokens):
                    logits=model(tokens[:,-context:])[0][0,-1].float()
                    logits[tokenizer.token_to_id('<|bos|>')]=float('-inf')
                    logits[blueprint['pad_id']]=float('-inf')
                    if body.temperature==0:token=int(logits.argmax())
                    else:
                        values,indices=torch.topk(logits/min(1.5,max(.05,body.temperature)),min(40,len(logits)))
                        token=int(indices[torch.multinomial(values.softmax(-1),1,generator=generator)])
                    if token==blueprint['eos_id']:reason='end-of-document';break
                    generated.append(token)
                    tokens=torch.cat((tokens,torch.tensor([[token]])),dim=1)
            return {'text':tokenizer.decode(generated,skip_special_tokens=True),'tokens':len(generated),
                    'elapsed_seconds':time.monotonic()-began,'prompt_tokens':prompt_tokens,
                    'prompt_truncated':prompt_tokens>context,'checkpoint_sha256':info['checkpoint_sha256'],
                    'step':info['step'],'seed':body.seed,'finish_reason':reason}
        finally:lock.release()
    return app


if __name__=='__main__':
    import uvicorn
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--dataset',type=Path,required=True);p.add_argument('--html',type=Path,required=True)
    a=p.parse_args()
    uvicorn.run(application(a.root,a.dataset,a.html),host='127.0.0.1',port=8886,access_log=False)
