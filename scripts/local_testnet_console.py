"""Public observer for protocol-4 local training, with a GPU verification control plane."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse,JSONResponse,FileResponse
import uvicorn
from dawl_cluster.client import Client
from dawl_cluster.state import summary
from dawl_node.common import read_json

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/observer'))
p.add_argument('--telemetry',type=Path,default=Path('/telemetry/status.json'));p.add_argument('--host',default='127.0.0.1')
p.add_argument('--port',type=int,default=8882);args=p.parse_args()
cache={'loading':True,'errors':[]};client=None


async def observe():
    global cache
    async with httpx.AsyncClient(timeout=3,trust_env=False) as http:
        while True:
            errors=[];state=None
            try:state=await client.state()
            except Exception as error:errors.append('Current quorum certificate unavailable: '+str(error)[:140])
            try:telemetry=read_json(args.telemetry)
            except (OSError,ValueError):telemetry={'nodes':[],'gpu':None,'errors':['Waiting for host telemetry'],'observed_at':None}
            try:
                local=read_json(args.telemetry.parent/'finetune.json')
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(local['observed_at'])).total_seconds()
                if age<30:telemetry['nodes']+=local['nodes']
            except (OSError,ValueError,KeyError):pass
            async def rpc_status(i,url):
                value={'name':'validator-'+str(i),'url':url,'reachable':False}
                try:
                    response=await http.get(url+'/status');response.raise_for_status();result=response.json()['result']
                    value.update(reachable=True,height=int(result['sync_info']['latest_block_height']),catching_up=result['sync_info']['catching_up'])
                except Exception:pass
                return value
            validators=await asyncio.gather(*(rpc_status(i,u) for i,u in enumerate(client.config['rpcs'])))
            previous=cache.get('last_verified_state')
            cache={'loading':False,'observed_at':datetime.now(timezone.utc).isoformat(),'certified':state is not None,
                   'state':state,'last_verified_state':state or previous,'summary':summary(state) if state else None,
                   'telemetry':telemetry,'validators':validators,'errors':errors+telemetry.get('errors',[]),
                   'genesis_sha256':client.config['genesis_sha256'],
                   'scope':'One local host and one shared GPU. Quorum-certified ledger metadata; independently observed container/GPU telemetry. Full replay receipts remain admitted-node attestations.'}
            await asyncio.sleep(4)


@asynccontextmanager
async def lifespan(app):
    global client
    client=Client(args.root);task=asyncio.create_task(observe())
    try:yield
    finally:task.cancel();await asyncio.gather(task,return_exceptions=True);await client.close()


app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)


@app.get('/api/status')
@app.get('/api/gpu')
async def status():
    value=dict(cache);now=datetime.now(timezone.utc)
    value['snapshot_age_seconds']=(now-datetime.fromisoformat(value['observed_at'])).total_seconds() if value.get('observed_at') else None
    observed=value.get('telemetry',{}).get('observed_at')
    value['telemetry_age_seconds']=(now-datetime.fromisoformat(observed)).total_seconds() if observed else None
    if value['snapshot_age_seconds'] is not None and value['snapshot_age_seconds']>30:
        value.update(certified=False,state=None,errors=value['errors']+['Observer snapshot stale; showing historical state only.'])
    return JSONResponse(value,headers={'Cache-Control':'no-store'})


@app.get('/api/bootstrap')
async def bootstrap():
    return {'genesis':read_json(args.root/'genesis.json'),'genesis_sha256':client.config['genesis_sha256']}


@app.get('/api/certificate')
async def certificate():
    minimum=(cache.get('state') or cache.get('last_verified_state') or {}).get('height',0)
    for endpoint in client.config['rpcs']:
        try:
            value=await client.certificate(endpoint,minimum);client.accept_certificate(value);return value
        except (OSError,ValueError,TimeoutError):pass
    raise HTTPException(503,'No fresh quorum certificate available')


@app.get('/')
@app.get('/gpu')
async def index():return HTMLResponse(Path(__file__).with_suffix('.html').read_text())


@app.get('/experiments')
async def experiments():return HTMLResponse(Path('/experiments.html').read_text())


@app.get('/branches')
async def branches():return HTMLResponse(Path('/branches.html').read_text())


@app.get('/api/collaboration-rehearsal')
async def collaboration_rehearsal():
    root=Path('/collaboration-rehearsal')
    if not (root/'verification.json').is_file():raise HTTPException(404,'No archived collaboration rehearsal mounted')
    return JSONResponse({k:read_json(root/(k+'.json')) for k in ('state','verification','selections','resolved')},
                        headers={'Cache-Control':'no-store'})


@app.get('/api/experiments')
async def experiment_data():
    return JSONResponse(read_json(Path('/presentation/index.json')),headers={'Cache-Control':'no-store'})


@app.get('/evidence/{evidence_id}')
async def evidence(evidence_id: str):
    # Only explicit public archive entries, never arbitrary filesystem paths.
    item=read_json(Path('/presentation/index.json'))['evidence'].get(evidence_id)
    if not item:raise HTTPException(404,'Unknown public evidence item')
    path=Path('/presentation/evidence')/item['file']
    return FileResponse(path,media_type='application/json' if item['format']=='json' else 'text/plain',
                        headers={'X-Content-Type-Options':'nosniff','X-Evidence-SHA256':item['sha256']})


if __name__=='__main__':uvicorn.run(app,host=args.host,port=args.port,access_log=False)
