"""Capture certified corpus completion, pod placement and per-worker counters."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from control import REPO, save
from launch import command


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=REPO/'artifacts/azure-pilot-001')
    a=p.parse_args();root=a.root.resolve()
    deployment=json.loads((root/'deployment.json').read_text())
    launched=json.loads((root/'launched.json').read_text())
    pins=json.loads((root/'network-pins.json').read_text())
    os.environ['KUBECONFIG']=str(root/'kubeconfig')
    os.environ['PATH']=str(REPO/'artifacts/azure-tools')+os.pathsep+str(REPO/'artifacts/azure-tools/Scripts')+os.pathsep+os.environ['PATH']
    kube=REPO/'artifacts/azure-tools/kubectl.exe'
    def k(*args): return command(kube,*args)
    def cli(verb):
        return json.loads(k('-n','dawl','exec','publisher-0','--','python','/app/dawl_cluster.py',verb,'--root','/data/node'))
    results=root/'results';results.mkdir(exist_ok=True)
    expiry=datetime.fromisoformat(deployment['expires_at'].replace('Z','+00:00'))
    while datetime.now(timezone.utc)<expiry:
        status=cli('status')
        with (results/'progress.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(), **status))+'\n')
        print(f"Height {status['height']}: {status['complete']}/{status['jobs']} jobs, {status['attestations']} attestations",flush=True)
        if status['disputed']: raise RuntimeError('disputed data; inspect receipts before publication')
        if status['complete']==status['jobs']: break
        time.sleep(15)
    else: raise TimeoutError('deadline reached; no successful corpus completion claimed')
    released=cli('publish');save(results/'release.json',released)
    certificate=json.loads(k('-n','dawl','exec','publisher-0','--','dawl-consensus','verify',
        '--genesis','/data/node/genesis.json','--genesis-sha256',pins['genesis_sha256'],
        '--rpc','http://10.240.1.12:26657','--max-age','120s'))
    if not certificate['verified'] or certificate['state']['dataset_release']['id']!=released['dataset_release']:
        raise ValueError('dataset release certificate mismatch')
    save(results/'public-certified-state.json',certificate)
    status=cli('status');save(results/'status.json',status)
    pods=json.loads(k('-n','dawl','get','pods','-o','json'))
    placement=[dict(name=x['metadata']['name'],host=x['spec'].get('nodeName'),phase=x['status']['phase']) for x in pods['items']]
    validators=[x for x in placement if x['name'].startswith('validator-')]
    if len(validators)!=4 or len({x['host'] for x in validators})!=4:
        raise ValueError('validators did not occupy four distinct AKS nodes')
    save(results/'placement.json',placement)
    def counters(i):
        value=json.loads(k('-n','dawl','exec',f'worker-{i}-0','--','python','-c',
          "import sqlite3,json; print(json.dumps(dict(sqlite3.connect('file:/data/node/worker.sqlite?mode=ro',uri=True).execute('SELECT name,value FROM counters'))))"))
        return f'worker-{i}',value
    with ThreadPoolExecutor(max_workers=8) as pool: counts=dict(pool.map(counters,range(20)))
    save(results/'worker-counters.json',counts)
    try: (results/'resource-usage.txt').write_bytes(k('-n','dawl','top','pods'))
    except RuntimeError: pass
    seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(launched['started_at'])).total_seconds()
    save(results/'report.json',dict(completed=True,workers=20,seconds=seconds,
        corpus_chunks=status['corpus_chunks'],corpus_tokens=status['corpus_tokens'],attestations=status['attestations'],
        verified_chunk_executions=status['verified_chunk_executions'],dataset_release=released['dataset_release'],
        training_executed=False,limits=['One Azure subscription and operator',
            'Worker-start-to-observation elapsed time includes orchestration/transfer; not an isolated compute benchmark',
            'No baseline comparison or 5x throughput claim', 'No cloud fault injection in this completion capture']))
    print('Certified full-corpus completion captured in '+str(results))


if __name__=='__main__': main()
