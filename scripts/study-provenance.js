export function showProvenance(target, doc, record) {
  target.replaceChildren();
  const title=document.createElement('h2');title.textContent=doc.title;
  const intro=document.createElement('p');intro.textContent=doc.text;
  target.append(title,intro);
  if(!doc.steps.length)return;
  const list=document.createElement('ol');list.className='history';
  for(const step of doc.steps){
    const item=document.createElement('li'),details=document.createElement('details'),summary=document.createElement('summary'),p=document.createElement('p');
    summary.textContent=step.title;p.textContent=step.text;details.append(summary,p);
    details.addEventListener('toggle',()=>{if(details.open)record('documentation_open')});
    if(step.evidence){const a=document.createElement('a');a.textContent='Se registrert dokument';a.href=step.evidence;a.target='_blank';a.rel='noopener';a.addEventListener('click',()=>record('evidence_open'));details.append(a)}
    item.append(details);list.append(item);
  }
  target.append(list);
}
