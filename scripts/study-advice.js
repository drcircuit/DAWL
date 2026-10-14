export function showAdvice(target, trial) {
  target.replaceChildren();
  const heading=document.createElement('h1');heading.textContent=trial.title;
  const situation=document.createElement('p');situation.className='situation';situation.textContent=trial.situation;
  const label=document.createElement('h2');label.textContent='Rådet du skal vurdere';
  const advice=document.createElement('blockquote');advice.textContent=trial.advice;
  target.append(heading,situation,label,advice);
}
