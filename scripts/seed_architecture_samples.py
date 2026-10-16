"""Record one unchanged continuation from each running Seed model studio."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen


def request(url, body=None):
    raw = None if body is None else json.dumps(body).encode()
    req = Request(url, data=raw, headers={'Content-Type':'application/json'})
    with urlopen(req, timeout=120) as response:
        return json.load(response)


root = Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,default=root/'docs/results/seed-architecture-samples-001')
p.add_argument('--tokens',type=int,default=32)
p.add_argument('--prompt',default='The traveller looked up at the stars and')
a=p.parse_args()
if not 1<=a.tokens<=256:raise ValueError('Invalid token budget')
out = a.output/'samples.json'
if out.exists():
    raise ValueError('Preserve the recorded examples; use a fresh experiment directory for a new set')
params = {'prompt':a.prompt, 'max_tokens':a.tokens, 'temperature':.7, 'seed':42}
result = {'schema':'hypertrix.seed-architecture-samples/1', 'created_at':datetime.now(timezone.utc).isoformat(),
          'purpose':'One illustrative sample per configuration; no cherry-picking or edits; not a controlled quality benchmark.',
          'parameters':params, 'samples':[]}
models = request('http://127.0.0.1:8886/api/models')['models']
for model_id, label in [('dense','Seed I dense'),('moe','Seed I MoE'),('seed2','Seed II')]:
    metadata = next(m for m in models if m['id'] == model_id)
    answer = request('http://127.0.0.1:8886/api/complete',dict(params,model=model_id))
    result['samples'].append({'label':label, 'request':dict(params,model=model_id), 'response':answer,
                               'config':metadata['config'], 'sampler':'top-k=40; no repetition penalty'})
body = dict(params,top_p=.9,repetition_penalty=1.1)
answer = request('http://127.0.0.1:8887/api/complete',body)
result['samples'].append({'label':'Seed III', 'request':body,'response':answer,
                           'sampler':'top-p=0.9; repetition penalty=1.1'})
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
