"""Read-only control-plane observatory. No signing keys or container-runtime socket."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,'/app/src')
import httpx
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
import uvicorn
from dawl_cluster.client import Client
from dawl_cluster.state import summary
from dawl_cluster.contributions import endpoint
from dawl_node.common import sha256,parse_json,verify_signature

ROOT=Path('/observer')
EXCHANGE=Path('/exchange')
cache={'loading':True,'nodes':[],'validators':[],'errors':[],'state':None}
previous={}


def cpu_usage(value):
    raw=value.get('resources',{})
    if raw.get('cpu_usage_ns'): return int(raw['cpu_usage_ns'])/1e9
    lines=dict(line.split() for line in raw.get('cpu_stat','').splitlines() if len(line.split())==2)
    return int(lines['usage_usec'])/1e6 if 'usage_usec' in lines else None


async def observe():
    global cache
    topology=json.loads((ROOT/'topology.json').read_text())
    client=Client(ROOT)
    async with httpx.AsyncClient(timeout=5,trust_env=False) as http:
        try:
            while True:
                errors=[];state=None;nodes={}
                try: state=await client.state()
                except Exception as error: errors.append('Certified state: '+str(error)[:180])
                for path in [*(EXCHANGE/'status').glob('*.json'),*(EXCHANGE/'contributors').glob('*/*.json')]:
                    if path.stat().st_size<100000:
                        try:nodes[path.stem]=json.loads(path.read_text())
                        except (OSError,ValueError):pass
                gateway=False
                try:
                    response=await http.get('http://127.70.0.31:8901/status');response.raise_for_status()
                    nodes.update({n['name']:n for n in response.json()['nodes']});gateway=True
                except Exception as error: errors.append('Cloud telemetry: '+str(error)[:140])
                if os.environ.get('DAWL_SITE')=='cloud':
                    try:
                        response=await http.get('http://127.70.0.32:8902/api/site-telemetry');response.raise_for_status()
                        nodes.update({n['name']:n for n in response.json()['nodes']})
                    except Exception as error:errors.append('Local telemetry: '+str(error)[:140])
                async def validator(node):
                    result=dict(name=node['name'],site=node['site'],host=node['host'],reachable=False)
                    try:
                        start=time.perf_counter()
                        status=(await http.get('http://'+node['host']+':26657/status')).json()['result']
                        peers=(await http.get('http://'+node['host']+':26657/net_info')).json()['result']
                        result.update(reachable=True,height=int(status['sync_info']['latest_block_height']),
                            app_hash=status['sync_info']['latest_app_hash'],catching_up=status['sync_info']['catching_up'],
                            peers=[p['node_info']['moniker'] for p in peers['peers']],request_pair_ms=round(1000*(time.perf_counter()-start),1))
                    except Exception as error: result['error']=str(error)[:150]
                    return result
                validators=await asyncio.gather(*(validator(n) for n in topology['nodes'] if n['role']=='validator'))
                all_nodes=[];now=datetime.now(timezone.utc);clock=time.monotonic()
                specs=list(topology['nodes'])
                for public,provider in (state or {}).get('providers',{}).items():
                    specs.append(dict(name=provider['name'],role='contributor',site='local',public_key=public,
                                      endpoint=provider['endpoint'],capabilities=provider['capabilities']))
                for spec in specs:
                    value={**spec,**nodes.get(spec['name'],{})}
                    observed=value.get('observed_at')
                    value['age_seconds']=round((now-datetime.fromisoformat(observed)).total_seconds(),1) if observed else None
                    usage=cpu_usage(value);last=previous.get(spec['name'])
                    value['cpu_cores']=round(max(0,(usage-last[1])/(clock-last[0])),3) if usage is not None and last and usage>=last[1] else None
                    if usage is not None: previous[spec['name']]=(clock,usage)
                    all_nodes.append(value)
                last_good=cache.get('last_good_state')
                if state: last_good=summary(state)
                cache=dict(loading=False,observed_at=now.isoformat(),certified=state is not None,
                    state=summary(state) if state else None,last_good_state=last_good,jobs=state['jobs'] if state else {},
                    actors=state['plan']['actors'] if state else {},nodes=all_nodes,validators=validators,
                    gateway_reachable=gateway,errors=errors,topology=topology,
                    network=json.loads((EXCHANGE/'bundle/network.json').read_text()),
                    scope='Node telemetry is observational. Ledger state is quorum-verified against the pinned genesis. Signed DHT role discovery uses an invited SSH mesh. Existing auditor/Oracle admission stays fixed; certificates, reputation and rewards are deferred. No training.')
                await asyncio.sleep(5)
        finally: await client.close()


@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(observe())
    try: yield
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)


app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)


@app.get('/api/status')
async def status():
    value=dict(cache)
    observed=value.get('observed_at')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(observed)).total_seconds() if observed else None
    value['snapshot_age_seconds']=round(age,1) if age is not None else None
    if age is not None and age>30:
        value.update(certified=False,state=None,jobs={},gateway_reachable=False,
                     errors=[*value['errors'],'Observer snapshot is stale; displayed history is not current state.'])
    return JSONResponse(value,headers={'Cache-Control':'no-store'})


@app.get('/api/bootstrap')
async def bootstrap():
    return {name:json.loads((EXCHANGE/'bundle'/name).read_text()) for name in ('network.json','genesis.json','registrations.json')}


@app.get('/api/site-telemetry')
async def site_telemetry():
    nodes=[]
    for path in [*(EXCHANGE/'status').glob('*.json'),*(EXCHANGE/'contributors').glob('*/*.json')]:
        try:
            if path.stat().st_size<100000:nodes.append(json.loads(path.read_text()))
        except (OSError,ValueError):pass
    return {'nodes':nodes}


@app.get('/api/ledger')
async def ledger(): return json.loads((EXCHANGE/'bundle/lineage.json').read_text())


@app.get('/api/contributions/{identity}')
async def contribution(identity:str):
    state=cache.get('state') or cache.get('last_good_state') or {}
    batch=state.get('contributions',{}).get(identity)
    if batch is None:raise HTTPException(404,'unknown committed contribution')
    provider=state['providers'][batch['provider']];origin=endpoint(provider['endpoint']);descriptor=batch['manifest']
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False) as http:
            async with http.stream('GET',origin+'/api/objects/'+descriptor['sha256']) as response:
                response.raise_for_status();data=bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data)>descriptor['bytes']:raise ValueError('manifest exceeds commitment')
        if len(data)!=descriptor['bytes'] or sha256(data)!=descriptor['sha256']:raise ValueError('manifest differs from commitment')
        envelope=parse_json(bytes(data));body=verify_signature(envelope,batch['provider'])
        if body['catalog']!=batch['catalog']:raise ValueError('manifest catalog differs from ledger')
        return {'contribution':identity,'committed':batch,'signed_manifest':envelope}
    except (httpx.HTTPError,ValueError,KeyError):raise HTTPException(502,'committed provider manifest unavailable or invalid')


@app.get('/')
async def index(): return HTMLResponse(PAGE)


PAGE=r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>The Hypertrix Console</title>
<style>
:root{font:14px system-ui;color:#dce6f4;background:#0c1420}*{box-sizing:border-box}body{margin:0}header{padding:28px 36px;border-bottom:1px solid #29384b;background:#101e30}h1{font-size:27px;margin:4px 0 9px}h2{font-size:17px;margin:0 0 18px}h3{font-size:14px;margin:0 0 10px}p{line-height:1.5;color:#a7b8cc}a{color:#74c7ff}nav{display:flex;gap:20px;flex-wrap:wrap;margin-top:17px}main{max-width:1560px;margin:auto;padding:26px 36px}.eyebrow{color:#79d8bf;letter-spacing:.15em;font-size:11px}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:24px}.card,.panel{background:#122136;border:1px solid #293b53;border-radius:12px;padding:20px}.metric{font-size:27px;margin:9px 0;color:#f1f7ff}.label,.muted{color:#9db0c8;font-size:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{margin-bottom:20px;overflow:auto}.site{border:1px solid #344b67;border-radius:9px;padding:16px}.nodes{display:flex;gap:7px;flex-wrap:wrap}.chip{border:1px solid #45627f;padding:7px 9px;border-radius:5px;font:12px ui-monospace;background:#1a3048;color:#dce6f4;cursor:pointer}.ok{color:#83e8bd}.bad{color:#ff9e98}.warn{color:#ffcf7d}.bridge{text-align:center;padding:16px;color:#a9c8e5}.status{border-radius:20px;background:#183e37;color:#97eccb;padding:7px 12px;display:inline-block}.status.warn{background:#503d22;color:#ffcf7d}table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;color:#9eb6d3;font-weight:500;border-bottom:1px solid #3a4d65}td,th{padding:11px 9px;vertical-align:top}tbody tr{border-bottom:1px solid #23374f}tbody tr:hover{background:#19304a;cursor:pointer}code{font:12px ui-monospace;word-break:break-all}pre{font:12px ui-monospace;white-space:pre-wrap;word-break:break-word;max-height:55vh;overflow:auto;color:#bfd3ed}details{border-top:1px solid #304560;padding-top:14px;margin-top:15px}summary{cursor:pointer;color:#90cfff}.span2{grid-column:1/-1}progress{width:100%;height:12px;accent-color:#70d8b4}.errors{color:#ffbc8b}dialog{background:#122136;color:#dce6f4;border:1px solid #53749c;border-radius:12px;width:min(900px,92vw)}dialog::backdrop{background:#0009}button.close{float:right;color:white;background:#30445f;border:0;border-radius:4px;padding:8px 12px;cursor:pointer}.event{padding:10px 0;border-bottom:1px solid #293b53}.timestamp{font-size:11px;color:#8ea5c2}.footer{font-size:12px;color:#8fa5be}@media(max-width:950px){.cards{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.span2{grid-column:auto}main,header{padding:22px 16px}}
</style>
<header><div class="eyebrow">HYPERTRIX / THE COLLECTIVE</div><h1>The Hypertrix Console</h1><p>The Collective, made visible. Follow The Oracle, Cortex Units and Daemons across Azure and local compute.</p><span id="health" class="status warn">Connecting…</span> <span id="updated" class="muted"></span><nav><a href="#topology">The Collective</a><a href="#consensus">The Oracle</a><a href="#nodes">Containers</a><a href="#work">Execution Leases</a><a href="#provenance">The Blueprint</a><a href="/api/status">Raw telemetry</a></nav></header>
<main><div id="errors" class="errors"></div><div class="cards">
<div class="card"><div class="label">NODE CONTAINERS REPORTING</div><div class="metric" id="count">—</div><div class="muted">Independent state mounts and logs</div></div>
<div class="card"><div class="label">CONSENSUS HEIGHT</div><div class="metric" id="height">—</div><div class="muted">At least 3 of 4 votes to commit</div></div>
<div class="card"><div class="label">COMPLETED JOBS</div><div class="metric" id="complete">—</div><progress id="progress" max="26" value="0"></progress></div>
<div class="card"><div class="label">ACCEPTED CIPHER-LOGS</div><div class="metric" id="audits">—</div><div class="muted" id="replays">—</div></div>
<div class="card"><div class="label">AZURE SHARED ALLOCATION</div><div class="metric">3 vCPU</div><div class="muted">8 GB · Norway East · one group</div></div></div>
<div class="grid"><section id="topology" class="panel"><h2>The Collective · live topology</h2><div class="site"><h3>Azure · <span id="cloudCount">7</span> nodes + tunnel gateway</h3><div class="nodes" id="cloud"></div></div><div class="bridge" id="bridge">Separate consensus and artifact tunnels ↕</div><div class="site"><h3>Local · <span id="localCount">20</span> nodes + Console</h3><div class="nodes" id="local"></div></div><p class="muted">Two shared host failure domains. Container isolation does not imply independent machines or operators. Private validator adapters use filesystem sockets.</p></section>
<section id="consensus" class="panel"><h2>The Oracle · consensus control plane</h2><table><thead><tr><th>Validator</th><th>Height</th><th>Peers</th><th>RPC pair</th></tr></thead><tbody id="validators"></tbody></table><p id="quorum"></p><details><summary>Inspect current certified state</summary><pre id="state">Waiting for certificate…</pre></details></section>
<section id="nodes" class="panel span2"><h2>Container health and resource use</h2><table><thead><tr><th>Node / role</th><th>Site</th><th>Phase / age</th><th>CPU cores</th><th>Memory / limit</th><th>Received</th><th>Transfer retries</th><th>Certificate failures</th></tr></thead><tbody id="nodeRows"></tbody></table><p class="muted">Click a row for its image pin, container identity, resource counters and recent events. CPU is sampled cgroup consumption, not a reservation. Telemetry is not cryptographic proof.</p></section>
<section id="work" class="panel"><h2>Execution Leases &amp; Cipher-Logs</h2><p id="leases"></p><table><thead><tr><th>Job</th><th>Frags</th><th>Audits</th><th>Leases</th><th>Failures</th></tr></thead><tbody id="jobs"></tbody></table><p class="muted">Click a job to inspect its replay boundaries, lease holders, reports and transaction references.</p></section>
<section class="panel"><h2>Recent node events</h2><div id="events"></div></section>
<section class="panel span2"><h2>DHT discovery &amp; contributions</h2><p id="discovery"></p><div id="contributions"></div><details><summary>Blueprint release history</summary><pre id="releaseHistory"></pre></details></section>
<section id="provenance" class="panel span2"><h2>The Blueprint · bootstrap &amp; provenance</h2><p id="chain"></p><p id="release"></p><p><a href="/api/bootstrap">Inspect genesis and admitted identities</a> · <a href="/api/ledger">Inspect bootstrap Cipher-Logs: source → tokens → Synapses</a></p><details><summary>Inspect image pins and network manifest</summary><pre id="network"></pre></details><p class="muted"><b>Frags</b> are source/token chunks. <b>Synapses</b> are reconstructed input embeddings. <b>The Source Code Matrix</b> is the pinned shared embedding table. Cipher-Logs authenticate provenance and audit claims; they are not proof of computation.</p><p class="footer" id="scope"></p></section></div></main>
<dialog id="detail"><button class="close" onclick="document.getElementById('detail').close()">Close</button><h2 id="detailTitle"></h2><pre id="detailBody"></pre></dialog>
<script>
const by=id=>document.getElementById(id),text=(id,v)=>by(id).textContent=v;
function show(title,value){text('detailTitle',title);text('detailBody',JSON.stringify(value,null,2));by('detail').showModal()}
function row(parent,values,data,title){let r=document.createElement('tr');for(let v of values){let td=document.createElement('td');td.textContent=v;r.append(td)}if(data)r.onclick=()=>show(title,data);parent.append(r)}
function displayName(n){let i=n.name.split('-').at(-1);return n.role==='worker'?'Daemon '+i:n.role==='contributor'?'Contributor '+n.name:n.role==='provider'?'Cortex Unit '+i:n.role==='validator'?'Oracle validator '+i:'Blueprint Publisher'}
function mb(v){return v==null||v==='max'?'—':(Number(v)/1048576).toFixed(1)+' MB'}
async function refresh(){try{let response=await fetch('/api/status');if(!response.ok)throw Error('Telemetry endpoint unavailable');let d=await response.json();if(d.loading)return;
text('health',d.certified?'The Oracle · quorum certificate verified':'Current certificate unavailable');by('health').className='status'+(d.certified?'':' warn');text('updated',new Date(d.observed_at).toLocaleTimeString());text('errors',d.errors.join(' · '));
text('cloudCount',d.nodes.filter(n=>n.site==='cloud').length);text('localCount',d.nodes.filter(n=>n.site==='local').length);let s=d.state||d.last_good_state;text('count',d.nodes.filter(n=>n.age_seconds!==null&&n.age_seconds+(d.snapshot_age_seconds||0)<20&&n.phase==='running').length+' / '+d.nodes.length);
for(let id of ['cloud','local','validators','nodeRows','jobs','events'])by(id).replaceChildren();
for(let n of d.nodes){let chip=document.createElement('button');chip.className='chip';chip.textContent=displayName(n)+' · '+(n.phase||'unknown');chip.onclick=()=>show(n.name,n);by(n.site).append(chip);let r=n.resources||{},c=n.counters||{};row(by('nodeRows'),[displayName(n)+' / '+n.name,n.site,(n.phase||'unknown')+' / '+(n.age_seconds??'—')+'s',n.cpu_cores??'—',mb(r.memory_bytes)+' / '+mb(r.memory_limit_bytes),mb(c.received_bytes),c.piece_attempt_failures||0,c.certificate_failures||0],n,n.name)}
for(let v of d.validators)row(by('validators'),[v.name,v.reachable?v.height:'Unreachable',(v.peers||[]).length+' / 3',v.request_pair_ms===undefined?'—':v.request_pair_ms+' ms'],v,v.name);
text('bridge',d.gateway_reachable?'Consensus + bulk-data tunnels · gateway reachable ↕':'Cloud gateway telemetry unavailable');text('quorum',d.validators.filter(v=>v.reachable).length+' reachable validators. Reachability alone does not establish quorum or agreement.');
if(s){text('height',s.height.toLocaleString()+(d.certified?'':' (last verified)'));text('complete',s.complete+' / '+s.jobs);by('progress').max=s.jobs;by('progress').value=s.complete;text('audits',s.attestations);text('replays',s.verified_chunk_executions.toLocaleString()+' attested Frag replays');text('leases',s.leases+' active leases · '+s.expired_leases+' expired · '+s.disputed+' disputed · '+s.attempts+' attempts');text('state',JSON.stringify(s,null,2));text('chain',s.chain_id+' · plan '+s.plan_sha256);text('release',s.dataset_release?'The Blueprint · published dataset '+s.dataset_release.id:'The Blueprint awaits complete independent audit coverage.');}
for(let [id,j] of Object.entries(d.jobs||{})){let att=Object.keys(j.attestations).length;row(by('jobs'),[id.slice(0,12),j.spec.positions?.length??'—',att+' / 2',Object.keys(j.leases).length,j.failures.length],j,'Job '+id)}
let events=d.nodes.flatMap(n=>(n.events||[]).map(e=>({...e,node:n.name}))).sort((a,b)=>b.time-a.time).slice(0,18);for(let e of events){let el=document.createElement('div');el.className='event';el.textContent=e.node+' · '+e.kind+' · '+new Date(e.time*1000).toLocaleTimeString();el.onclick=()=>show(e.kind,e);by('events').append(el)}text('discovery',d.nodes.filter(n=>n.dht).length+' peers reporting signed role discovery · '+d.nodes.reduce((a,n)=>a+(n.dht?.contacts||0),0)+' routing contacts across peers. Advertised roles do not grant authority.');by('contributions').replaceChildren();for(let [id,c] of Object.entries(s?.contributions||{})){let el=document.createElement('p');el.textContent=id.slice(0,16)+' · '+c.catalog.books.map(b=>b.title).join(' · ');el.onclick=()=>show('Contribution '+id,c);by('contributions').append(el);let link=document.createElement('a');link.href='/api/contributions/'+id;link.textContent='Inspect signed source → token → Synapse lineage';by('contributions').append(link)}text('releaseHistory',JSON.stringify(s?.dataset_releases||[],null,2));text('network',JSON.stringify(d.network,null,2));text('scope',d.scope);
}catch(e){text('errors',e.message);text('health','Monitor connection lost');by('health').className='status warn'}}refresh();setInterval(refresh,5000);
</script></html>'''


if __name__=='__main__': uvicorn.run(app,host='127.0.0.1',port=8882,access_log=False)
