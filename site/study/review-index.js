async function load(){
 const r=await fetch('data/review.json');if(!r.ok)throw Error('Kunne ikke hente gjennomgangen. Prøv å laste siden på nytt.');const data=await r.json();
 document.getElementById('release').textContent=data.model_release;
 for(const c of data.cases){const section=document.createElement('section'),title=document.createElement('h3');section.className='scenario';title.textContent=c.title;section.append(title);
  for(const [variant,label] of [['good','Foreslått godt råd'],['bad','Foreslått dårlig råd']]){const row=document.createElement('div'),name=document.createElement('span');row.className='variant';name.textContent=label;row.append(name);
   for(const condition of 'ABC'){const a=document.createElement('a');a.textContent=condition;a.href='preview.html?'+new URLSearchParams({case_id:c.id,variant,condition});a.setAttribute('aria-label',`${c.title} – ${label} – dokumentasjon ${condition}`);row.append(a)}section.append(row)}
  document.getElementById('scenarios').append(section)
 }
}
load().catch(e=>document.getElementById('error').textContent=e.message);
