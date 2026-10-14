import {showAdvice} from './study-advice.js';
import {showProvenance} from './study-provenance.js';
const $=id=>document.getElementById(id),preview=location.pathname.endsWith('/preview.html');
let data,queue=[],position=0;
function question(name,title,options,anchors=''){
 const fs=document.createElement('fieldset'),legend=document.createElement('legend'),choices=document.createElement('div');legend.textContent=title;choices.className='choices';
 for(const [value,label] of options){const l=document.createElement('label'),i=document.createElement('input');l.className='choice';i.type='radio';i.name=name;i.value=value;i.required=true;l.append(i,document.createTextNode(label));choices.append(l)}
 fs.append(legend,choices);if(anchors){const a=document.createElement('div');a.className='anchors';a.textContent=anchors;fs.append(a)}return fs;
}
function render(t){
 $('intro').hidden=true;$('task').hidden=false;$('progress').textContent=preview?'Forhåndsvisning':`Oppgave ${position+1} av 3`;
 showAdvice($('advice'),t);showProvenance($('documentation'),t.documentation,()=>{});
 const seven=Array.from({length:7},(_,i)=>[i+1,String(i+1)]),q=$('questions');q.replaceChildren();
 q.append(question('decision','Hva ville du valgt i denne situasjonen?',t.options.map(o=>[o.id,o.label])),
 question('trust','Hvor stor tillit har du til rådet?',seven,'1 = svært liten tillit · 7 = svært stor tillit'),
 question('reliance','Hvordan ville du brukt rådet?',[['follow','Fulgt rådet'],['check','Sjekket det nærmere'],['reject','Avvist rådet']]),
 question('confidence','Hvor sikker er du på ditt eget valg?',seven,'1 = svært usikker · 7 = svært sikker'),
 question('difficulty','Hvor vanskelig var oppgaven?',seven,'1 = svært lett · 7 = svært vanskelig'));
 $('assessment').reset();$('next').textContent=position===2?'Fullfør gjennomgangen':'Neste oppgave →';
 $('next').disabled=preview;if(preview)$('preview-note').textContent='Forhåndsvisning av én oppgave.';window.scrollTo(0,0);
}
function randomIndex(n){return crypto.getRandomValues(new Uint32Array(1))[0]%n}
$('start').addEventListener('click',()=>{
 const condition=$('condition').value;queue=data.cases.map(c=>data.trials[`${c.id}/${randomIndex(2)?'good':'bad'}/${condition}`]);
 for(let i=queue.length-1;i>0;i--){const j=randomIndex(i+1);[queue[i],queue[j]]=[queue[j],queue[i]]}position=0;render(queue[0]);
});
$('assessment').addEventListener('submit',e=>{
 e.preventDefault();if(preview)return;
 // Deliberately do not read, serialize, retain or transmit responses.
 $('assessment').reset();position++;
 if(position===queue.length){$('task').hidden=true;$('done').hidden=false;$('questions').replaceChildren();queue=[];$('progress').textContent='Fullført';window.scrollTo(0,0)}else render(queue[position]);
});
async function load(){
 $('start').disabled=true;const r=await fetch('data/review.json');if(!r.ok)throw Error('Kunne ikke hente oppgavene. Prøv å laste siden på nytt.');data=await r.json();$('start').disabled=false;
 if(preview){const p=new URLSearchParams(location.search),t=data.trials[`${p.get('case_id')}/${p.get('variant')}/${p.get('condition')}`];if(!t)throw Error('Ukjent forhåndsvisning. Velg en oppgave i forskeroversikten.');render(t)}
}
load().catch(e=>{$('intro').hidden=true;$('error').textContent=e.message});
