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
