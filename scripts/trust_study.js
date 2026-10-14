import {showAdvice} from './study-advice.js';
import {showProvenance} from './study-provenance.js';
const $=id=>document.getElementById(id);let session=null,trial=null;
const preview=location.pathname==='/study-preview';
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Lab-Token':window.STUDY_CSRF},body:body?JSON.stringify(body):undefined});const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Kontroller at alle feltene er utfylt.');return v}
function fail(e){$('error').textContent=e.message}
function question(name,title,options,anchors=''){
 const fs=document.createElement('fieldset'),legend=document.createElement('legend'),choices=document.createElement('div');legend.textContent=title;choices.className='choices';
 for(const [value,label] of options){const l=document.createElement('label'),i=document.createElement('input');l.className='choice';i.type='radio';i.name=name;i.value=value;i.required=true;l.append(i,document.createTextNode(label));choices.append(l)}
 fs.append(legend,choices);if(anchors){const a=document.createElement('div');a.className='anchors';a.textContent=anchors;fs.append(a)}return fs;
}
function render(t){
 trial=t;$('intro').hidden=true;$('error').textContent='';
 if(t.complete){$('task').hidden=true;$('done').hidden=false;$('progress').textContent='Fullført';$('receipt').textContent='Øktkode: '+session.session_id;sessionStorage.removeItem('hypertrix-study-v2');return}
 $('task').hidden=false;$('progress').textContent=`Oppgave ${t.ordinal+1} av ${t.total}`;
 showAdvice($('advice'),t);showProvenance($('documentation'),t.documentation,kind=>{if(!preview)api('/api/study/event',{...session,ordinal:t.ordinal,kind}).catch(fail)});
 const seven=Array.from({length:7},(_,i)=>[i+1,String(i+1)]),q=$('questions');q.replaceChildren();
 q.append(question('decision','Hva ville du valgt i denne situasjonen?',t.options.map(o=>[o.id,o.label])),
 question('trust','Hvor stor tillit har du til rådet?',seven,'1 = svært liten tillit · 7 = svært stor tillit'),
 question('reliance','Hvordan ville du brukt rådet?',[['follow','Fulgt rådet'],['check','Sjekket det nærmere'],['reject','Avvist rådet']]),
 question('confidence','Hvor sikker er du på ditt eget valg?',seven,'1 = svært usikker · 7 = svært sikker'),
 question('difficulty','Hvor vanskelig var oppgaven?',seven,'1 = svært lett · 7 = svært vanskelig'));
 $('assessment').reset();$('save').textContent=t.ordinal===2?'Lagre siste vurdering':'Lagre og fortsett →';
 if(preview){$('save').disabled=true;$('preview-note').textContent='Forhåndsvisning lagrer ingen svar.'}
 window.scrollTo(0,0);
}
$('consent').addEventListener('change',()=>{$('start').disabled=!$('consent').checked});
$('start').addEventListener('click',async()=>{try{$('start').disabled=true;session=await api('/api/study/start',{consent:true});sessionStorage.setItem('hypertrix-study-v2',JSON.stringify(session));render(await api('/api/study/current',session))}catch(e){fail(e);$('start').disabled=false}});
$('assessment').addEventListener('submit',async e=>{e.preventDefault();if(preview)return;$('save').disabled=true;try{const data=Object.fromEntries(new FormData(e.target));for(const n of ['trust','confidence','difficulty'])data[n]=Number(data[n]);await api('/api/study/submit',{...session,ordinal:trial.ordinal,response:data});render(await api('/api/study/current',session))}catch(e){fail(e)}finally{$('save').disabled=false}});
if(preview){$('preview').hidden=false;$('preview').textContent='FORSKERFORHÅNDSVISNING · Ingen deltakerdata lagres.';api('/api/study/preview'+location.search).then(render).catch(fail)}else{
 try{session=JSON.parse(sessionStorage.getItem('hypertrix-study-v2'));if(session)api('/api/study/current',session).then(render).catch(fail)}catch(e){fail(e)}
}
