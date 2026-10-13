"""Read-only loopback observatory for the consensus cluster."""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dawl_cluster.client import Client
from dawl_cluster.state import summary

ROOT=Path(__file__).resolve().parents[1]/"artifacts/cluster-v1/publisher"
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@app.get("/api/status")
async def status():
    client=Client(ROOT)
    try:
        async def observe(index,url):
            try:
                state=await client.state(url)
                return {"name":f"validator-{index}","reachable":True,"summary":summary(state)}
            except (OSError,ValueError) as error:
                return {"name":f"validator-{index}","reachable":False,"error":str(error)[:120]}
        validators=await asyncio.gather(*(observe(i,u) for i,u in enumerate(client.config["rpcs"])))
        reachable=[v for v in validators if v["reachable"]]
        best=max(reachable,key=lambda v:v["summary"]["height"])["summary"] if reachable else None
        return {"validators":validators,"state":best,"quorum":3,"configured_validators":4,
                "scope":"Live RPC observations. Use the public verifier to check a state against a signed quorum certificate.",
                "training":"Recipe planned; no training executed. Imported parent model history remains unverified."}
    finally: await client.close()

@app.get("/",response_class=HTMLResponse)
def page():
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DAWL consensus cluster</title>
<style>body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f6f7;color:#15212b}main{max-width:1080px;margin:40px auto;padding:0 24px}h1{font-size:34px;letter-spacing:-1px;margin-bottom:8px}h2{font-size:20px;margin-top:30px}.muted,small{color:#596671}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card{background:white;border:1px solid #dae0e4;padding:20px;border-radius:8px}.number{font-size:30px;font-weight:650}progress{width:100%;height:20px;accent-color:#087f8c}table{border-collapse:collapse;width:100%;background:white}td,th{text-align:left;padding:12px;border-bottom:1px solid #e0e5e8}th{font-size:13px;color:#596671}.good{color:#05705c}.bad{color:#a53d20}code{font-size:12px;overflow-wrap:anywhere}a{color:#007d89}#error{color:#a53d20}@media(max-width:650px){.cards{grid-template-columns:repeat(2,1fr)}main{padding:0 15px}td{padding:8px}}</style>
<main><small>DAWL / COMPUTE AND PROVENANCE</small><h1>Consensus cluster</h1><p class="muted">Four validators agree on work allocation and published lineage. Independent workers replay the corpus.</p>
<p><a href="http://127.0.0.1:8880/">Open source and vector observatory</a> · <a href="/api/status">Inspect JSON</a></p><p id="error"></p>
<div class="cards"><div class="card">Validators reachable<div class="number" id="nodes">-</div><small>3 of 4 required to commit</small></div><div class="card">Shards complete<div class="number" id="jobs">-</div><small>Two auditor keys per shard</small></div><div class="card">Corpus tokens<div class="number" id="tokens">-</div><small>All eight locked books</small></div><div class="card">Block height<div class="number" id="height">-</div><small>Leases use consensus height</small></div></div>
<h2>Complete corpus coverage</h2><progress id="progress" max="26" value="0"></progress><p id="coverage"></p>
<h2>Validator state</h2><table><thead><tr><th>Validator</th><th>Reachable</th><th>Committed height</th><th>Application hash</th></tr></thead><tbody id="validators"></tbody></table>
<h2>Dataset and weight lineage</h2><div class="card"><p id="release"></p><p id="training"></p><p id="parent"></p><small id="boundary"></small></div>
<h2>Job safety</h2><p id="leases"></p><p class="muted">All services currently run on one physical host. This is an admitted cluster, with fixed validators and no public rewards. Matching audit claims are not a proof of computation.</p><small id="scope"></small><p><code id="chain"></code></p></main>
<script>const byId=x=>document.getElementById(x);const set=(id,value)=>byId(id).textContent=value;function cell(row,value){let td=document.createElement('td');td.textContent=value;row.appendChild(td)}async function refresh(){try{const r=await fetch('/api/status');if(!r.ok)throw Error('Observatory unavailable');const d=await r.json();set('nodes',d.validators.filter(v=>v.reachable).length+' / 4');const body=byId('validators');body.replaceChildren();for(const v of d.validators){let row=document.createElement('tr');cell(row,v.name);cell(row,v.reachable?'Yes':'No');cell(row,v.summary?.height??'-');cell(row,v.summary?.app_hash.slice(0,22)??v.error);body.appendChild(row)}if(!d.state){set('error','No validator currently reachable.');return}const s=d.state;set('error','');set('jobs',s.complete+' / '+s.jobs);set('tokens',s.corpus_tokens.toLocaleString());set('height',s.height.toLocaleString());byId('progress').max=s.jobs;byId('progress').value=s.complete;set('coverage',s.corpus_chunks.toLocaleString()+' source chunks · '+s.verified_chunk_executions.toLocaleString()+' attested chunk replays · '+s.attestations+' signed results');set('leases',s.leases+' active leases · '+s.expired_leases+' expired and reclaimable · '+s.disputed+' disputed shards');set('release',s.dataset_release?'Dataset release: '+s.dataset_release.id:'Dataset publication waits for complete independent coverage.');set('training',Object.keys(s.training_plans).length+' training recipe planned. No model training executed.');const w=Object.values(s.weights)[0]?.body;set('parent',w?'Parent model: '+w.upstream+' · '+w.bytes.toLocaleString()+' bytes · content-addressed safetensors blocks':'No parent weights registered');set('boundary',w?.boundary??'');set('chain',s.chain_id+' / plan '+s.plan_sha256);set('scope',d.scope)}catch(e){set('error',e.message)}}refresh();setInterval(refresh,4000);</script></html>'''

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=8882,access_log=False)
