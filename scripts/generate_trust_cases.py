"""Generate/capture actual responses through the local lab, with its budget cap."""
import argparse
import json
import re
import httpx
p=argparse.ArgumentParser();p.add_argument('--models',nargs='+',default=['open','openai','hypertrix']);p.add_argument('--url',default='http://127.0.0.1:8885');a=p.parse_args()
with httpx.Client(timeout=300,trust_env=False) as client:
    html=client.get(a.url).text;token=re.search(r"const TOKEN='([^']+)'",html).group(1)
    status=client.get(a.url+'/api/status').json()
    for model in a.models:
        if not any(m['id']==model and m['ready'] for m in status['models']):
            print(json.dumps({'model':model,'status':'not-ready'}),flush=True);continue
        for case in status['cases']:
            response=client.post(a.url+'/api/generate',headers={'X-Lab-Token':token},json={'model_id':model,'case_id':case['id']})
            if response.status_code!=200:
                print(json.dumps({'model':model,'case':case['id'],'status':response.status_code,'error':response.json().get('detail')}),flush=True)
                continue
            value=response.json()
            print(json.dumps({'model':model,'case':case['id'],'answer_sha256':value['answer_sha256'],'metadata':value['metadata']}),flush=True)
