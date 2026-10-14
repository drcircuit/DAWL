/* Participant-first disclosure. The evidence inspector remains optional. */
function participantHtml(p){
  if(!p)return '';
  return `<div class="participant-provenance"><h3>${esc(p.heading)}</h3><p>${esc(p.intro)}</p><ol class="provenance-trail" aria-label="Model production history">${p.steps.map((s,i)=>`<li class="trail-${esc(s.kind)}"><details><summary><span class="trail-number">${i+1}</span><span class="trail-title">${esc(s.title)}</span><span class="trail-status">${esc(s.status)}</span></summary><p>${esc(s.text)}</p></details></li>`).join('')}</ol><p class="provenance-coverage">${esc(p.coverage)}</p><details class="label-guide"><summary>How the checks work</summary><p>${esc(p.what_it_means)}</p><p><b>Provider description:</b> information published by the organisation that made the starting model.</p><p><b>Recorded by this lab:</b> the project has saved a record of what it did or received.</p><p><b>Checked locally:</b> a separate program has compared the files with their records.</p><p><b>Reproduced on this machine:</b> a fresh process repeated training and obtained matching saved weights.</p><p>${esc(p.who_checked)}</p></details><p class="provenance-reflection">${esc(p.reflection)}</p></div>`
}
function provenanceHtml(p){
  const technical=`<details class="technical-provenance"><summary>Optional: inspect the technical records</summary><p>${esc(p.summary)}</p>${(p.boundaries||[]).map(s=>`<p class="small muted">${esc(s)}</p>`).join('')}${(p.links||[]).map(l=>`<a class="source" target="_blank" rel="noopener" href="${esc(l.url)}">${esc(l.title)} ↗</a>`).join('')}</details>`;
  if(!p.participant)return `<h3>${esc(p.title)}</h3><p>${esc(p.summary||'')}</p>`;
  const books=p.participant.source_books;
  const sources=books?.length?`<details class="source-books"><summary>See the books used in the local continuation</summary><ul>${books.map(b=>`<li><a target="_blank" rel="noopener" href="${esc(/^https?:\/\//.test(b.url)?b.url:'#')}">${esc(b.title)}</a><span class="small muted"> · ${esc(b.author)} · ${esc(b.domain)}</span></li>`).join('')}</ul></details>`:'';
  return participantHtml(p.participant)+sources+technical;
}
