'use strict';
const $ = id => document.getElementById(id);
const fmt = n => Number(n).toLocaleString();
const size = n => n >= 1e9 ? (n/1e9).toFixed(2)+' GB' : (n/1e6).toFixed(2)+' MB';
const el = (tag, text, cls) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; if(cls)e.className=cls; return e; };
let state, selected, inspectedRef;
async function get(url){const r=await fetch(url); if(!r.ok)throw Error('Node returned '+r.status);return r.json();}
function badge(text, status){return el('span',text,'badge '+(status===true?'pass':status===false?'fail':''));}
async function inspect(book){
  selected=book.book_id;
  const ref=book.audit_entry||book.vectors_entry||book.tokens_entry||book.source;
  inspectedRef=ref;
  const data=await get('/api/lineage/'+ref);
  $('selection').textContent=book.title+' · '+data.entries.length+' signed records in this ancestry';
  $('lineage').replaceChildren();
  const order={audit:0,vectors:1,tokens:2,source:3,resource:4};
  data.entries.sort((a,b)=>order[a.body.kind]-order[b.body.kind]);
  for(const entry of data.entries){
    const b=entry.body,p=b.payload,box=el('article',undefined,'entry');
    box.append(el('h3',b.kind==='resource'?p.type:b.kind),el('p','Contributor: '+(state.identities[b.signer]||b.signer)),el('code',entry.id));
    box.append(el('p','Author sequence '+b.sequence+' · '+b.parents.length+' lineage parents'));
    for(const parent of b.parents)box.append(el('code','← '+parent.slice(0,20)+'…'));
    if(b.kind==='audit')box.append(badge(p.accepted?'Full replay passed':'Audit failed',p.accepted),el('p',p.accepted?fmt(p.checked_chunks)+' chunks checked byte for byte':p.error));
    if(b.kind==='vectors')box.append(el('p',size(p.dense_bytes_reconstructed)+' reconstructed · dense vectors retained: 0 bytes'));
    const raw=el('a','Signed record ↗');raw.href='/api/entries/'+entry.id;raw.target='_blank';raw.rel='noopener';box.append(raw);
    if(p.files){const details=el('details');details.append(el('summary','Download artifacts ('+Object.keys(p.files).length+')'));
      for(const [name,f]of Object.entries(p.files)){const link=el('a',name+' · '+fmt(f.bytes)+' bytes','artifact');link.href='/api/objects/'+f.sha256;details.append(link);}box.append(details);}
    $('lineage').append(box);
  }
}
async function refresh(){
  try{
    state=await get('/api/summary'); const books=state.books;
    $('connection').textContent='● Node responding · '+new Date().toLocaleTimeString();
    const metrics=[[books.length,'registered books'],[fmt(books.reduce((s,b)=>s+b.tokens,0)),'committed tokens'],[size(books.reduce((s,b)=>s+b.dense_bytes,0)),'input vectors reconstructed'],[books.filter(b=>b.accepted===true).length+'/'+books.length,'books independently replayed']];
    $('metrics').replaceChildren(...metrics.map(([n,label])=>{const d=el('div',undefined,'metric');d.append(el('strong',n),el('span',label));return d;}));
    $('books').replaceChildren();
    for(const b of books){const row=el('tr'),title=el('td'),button=el('button',b.title);button.addEventListener('click',()=>inspect(b).catch(showError));title.append(button);row.append(title,el('td',fmt(b.chunks)),el('td',fmt(b.tokens)));
      for(const [text,status] of [[b.tokens_entry?'Published':'Pending',!!b.tokens_entry||null],[b.vectors_entry?'Recoverable':'Pending',!!b.vectors_entry||null],[b.accepted===true?'Passed':b.accepted===false?'Failed':'Pending',b.accepted]]){const td=el('td');td.append(badge(text,status));row.append(td);}$('books').append(row);}
    $('checkpoint').textContent='Ledger: '+state.checkpoint.entries+' entries · set digest '+state.checkpoint.set_sha256;
    $('measurements').replaceChildren();
    for(const run of state.runs){const r=run.report;$('measurements').append(el('p',run.type==='token'?'CPU tokenization: '+r.jobs_completed+' book jobs · '+r.workers+' worker processes · '+r.seconds.toFixed(2)+' seconds.':'Vector recovery: '+r.provider+' · '+r.books+' books · '+r.seconds.toFixed(2)+' seconds.'));
      if(r.gpu_execution_profile)$('measurements').append(el('p','GPU execution confirmed by runtime profiling: '+JSON.stringify(r.gpu_execution_profile.gather_events_by_provider)));}
    if(state.benchmark){for(const row of state.benchmark.results)$('measurements').append(el('p','Scaling: '+row.workers+' workers · '+row.books+' books · median '+row.median_seconds.toFixed(2)+' s · '+row.speedup.toFixed(2)+'× speedup.'));}
    const chosen=books.find(b=>b.book_id===selected)||books[0];
    if(chosen&&(chosen.audit_entry||chosen.vectors_entry||chosen.tokens_entry||chosen.source)!==inspectedRef)await inspect(chosen);
  }catch(error){showError(error);}
}
function showError(error){$('connection').textContent='Node unavailable: '+error.message;}
refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);

async function refreshSwarm(){
  try {
    const swarm=await get('/api/swarm');
    $('swarm-scope').textContent=swarm.scope;
    const nodes=swarm.nodes||[], names=new Map(nodes.map(n=>[n.public,n.name]));
    $('swarm-nodes').replaceChildren();$('swarm-topology').replaceChildren();$('swarm-receipts').replaceChildren();
    if(!nodes.length)return;
    const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
    svg.setAttribute('viewBox','0 0 900 235');svg.setAttribute('role','img');svg.setAttribute('aria-label','Live peer connectivity reported by each node');
    const points=new Map(nodes.map((n,i)=>[n.public,{x:90+i*720/Math.max(1,nodes.length-1),y:125}]));
    const drawn=new Set();
    for(const n of nodes)for(const peer of n.state?.peers||[]){
      const key=[n.public,peer.public].sort().join(':');if(drawn.has(key)||!points.has(peer.public)||!peer.reachable||!n.reachable)continue;drawn.add(key);
      const a=points.get(n.public),b=points.get(peer.public),path=document.createElementNS(ns,'path');
      path.setAttribute('d',`M ${a.x} ${a.y} Q ${(a.x+b.x)/2} ${-Math.abs(a.x-b.x)/7} ${b.x} ${b.y}`);path.setAttribute('class','swarm-link');svg.append(path);
    }
    const receipts=new Map(),sets=new Set();let reachable=0,shared=0;
    for(const n of nodes){
      const p=points.get(n.public),circle=document.createElementNS(ns,'circle'),label=document.createElementNS(ns,'text');
      circle.setAttribute('cx',p.x);circle.setAttribute('cy',p.y);circle.setAttribute('r','13');circle.setAttribute('class',n.reachable?'swarm-online':'swarm-offline');
      label.setAttribute('x',p.x);label.setAttribute('y',p.y+37);label.setAttribute('text-anchor','middle');label.textContent=n.name;svg.append(circle,label);
      const box=el('article',undefined,'entry');box.append(el('h3',n.name),badge(n.reachable?'Responding':'Unavailable',n.reachable));
      if(n.reachable){reachable++;const s=n.state;sets.add(s.checkpoint.set_sha256);shared+=s.announcement?.body.inventory.length||0;
        box.append(el('p',s.roles.join(' / ')),el('p',s.checkpoint.entries+' lineage records · '+s.receipt_count+' receipts'),el('p','Worker: '+s.phase));
        box.append(el('p',(s.announcement?.body.inventory.length||0)+' artifacts advertised · '+fmt(s.counters.pieces_downloaded||0)+' pieces fetched'));
        box.append(el('p',fmt(s.counters.corrupt_pieces_rejected||0)+' corrupt pieces rejected · '+fmt(s.counters.piece_cache_hits||0)+' piece cache hits'));
        if(s.disputes.length)box.append(badge(s.disputes.length+' claim contradicted by local replay',false));
        for(const row of s.receipts){const id=row.envelope.id,old=receipts.get(id);if(!old||row.locally_executed)receipts.set(id,row);}
      }else box.append(el('p',n.error||'Waiting for a node response'));
      $('swarm-nodes').append(box);
    }
    $('swarm-topology').append(svg);
    $('swarm-availability').textContent=reachable+'/'+nodes.length+' nodes responding · '+sets.size+' reported ledger set(s) · '+shared+' artifact replicas advertised. Lines show at least one successful directed contact; inventories and connectivity are node reports.';
    const artifacts=[...new Set(nodes.flatMap(n=>n.state?.announcement?.body.inventory||[]))].sort();
    const heatmap=el('table'),heading=el('tr');heading.append(el('th','Artifact SHA-256'));
    for(const n of nodes)heading.append(el('th',n.name));heatmap.append(heading);
    for(const digest of artifacts){const row=el('tr'),label=el('td',digest.slice(0,16)+'…');label.title=digest;row.append(label);
      for(const n of nodes){const cell=el('td'),has=n.state?.announcement?.body.inventory.includes(digest),serves=n.state?.roles.includes('PROVIDER');cell.append(badge(!n.reachable?'Offline':has?(serves?'Advertised':'Cached'):'Missing',n.reachable&&has?true:null));row.append(cell);}heatmap.append(row);}
    $('swarm-heatmap').replaceChildren(heatmap);
    const rows=[...receipts.values()].sort((a,b)=>(a.envelope.body.outcome==='invalid'?-1:0)-(b.envelope.body.outcome==='invalid'?-1:0));
    for(const row of rows.slice(0,50)){const b=row.envelope.body,tr=el('tr'),target=b.vectors_entry===swarm.canary_entry?'LAB CANARY':b.vectors_entry.slice(0,12)+'…';
      tr.append(el('td',names.get(b.signer)||b.signer.slice(0,12)),el('td',target),el('td',b.positions.length+'/'+b.total_chunks));
      const status=el('td');status.append(badge(b.outcome,b.outcome==='pass'?true:b.outcome==='invalid'?false:null));tr.append(status,el('td',Number(b.execution_time_ms).toFixed(1)+' ms'));
      const detail=el('td',row.locally_executed?'Replayed on reporting node':'Signed claim');if(b.rechecks)detail.append(el('p','Rechecks '+b.rechecks.slice(0,12)+'…'));if(b.error)detail.append(el('p',b.error.slice(0,180)));tr.append(detail);$('swarm-receipts').append(tr);
    }
  }catch(error){$('swarm-scope').textContent='Swarm observer unavailable: '+error.message;}
}
refreshSwarm();setInterval(()=>{if(!document.hidden)refreshSwarm();},5000);
