"""Export accepted ledger state only after independent fixed-genesis verification.

The cloud is a read replica, not a consensus participant. No raw host telemetry,
participant data, node credentials or command endpoints are exported.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import canonical_json
from network_view import public_network

GENESIS_PIN='5764519561f755af339ee6a0898dee7e2f8fe5966f489bccdc72b500959bb9a9'


def validate_head(certificate, previous=None, now=None):
    now=now or datetime.now(timezone.utc)
    header=certificate['signed_header']['header']
    timestamp=datetime.fromisoformat(header['time'].replace('Z','+00:00'))
    age=(now-timestamp).total_seconds()
    if certificate['genesis_sha256']!=GENESIS_PIN or not -30<=age<=120:
        raise ValueError('Wrong chain or stale/future quorum header')
    state=certificate['state'];height=state['height']
    digest=hashlib.sha256(canonical_json(state)).hexdigest()
    if height+1!=int(header['height']) or state['chain_id']!=header['chain_id']:
        raise ValueError('Header/state boundary mismatch')
    if previous:
        if previous['genesis_sha256']!=GENESIS_PIN:raise ValueError('Refusing implicit chain switch')
        if height<previous['height']:raise ValueError('Refusing rollback')
        if height==previous['height'] and digest!=previous['app_hash']:raise ValueError('Conflicting accepted head')
    return height,digest


def wsl_path(path):
    return subprocess.check_output(['wsl','-d','kali-linux','-u','root','--exec','wslpath','-a',str(path)],text=True).strip()


def capture(output, previous=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with httpx.Client(timeout=40,trust_env=False) as client:
        response=client.get('http://127.0.0.1:8882/api/certificate');response.raise_for_status();certificate=response.json()
    height,digest=validate_head(certificate,previous)
    # Preserve canonical state bytes: the Go verifier hashes the RawMessage.
    raw=canonical_json(certificate);path=output/'candidate-certificate.json';path.write_bytes(raw)
    genesis=ROOT/'docs/results/oracle-training-run-003/genesis.json'
    command=['wsl','-d','kali-linux','-u','root','--exec',wsl_path(ROOT/'artifacts/check-certificate'),
             '--genesis',wsl_path(genesis),'--genesis-sha256',GENESIS_PIN,'--certificate',wsl_path(path)]
    checked=subprocess.run(command,capture_output=True,text=True,timeout=40)
    if checked.returncode:raise ValueError('Independent quorum checker rejected snapshot: '+checked.stderr[:300])
    receipt=json.loads(checked.stdout)
    if not receipt['verified'] or receipt['state_height']!=height or receipt['app_hash']!=digest:raise ValueError('Checker result differs')
    state=certificate['state']
    stages=[]
    for stage_id,stage in state.get('training_stages',{}).items():
        stages.append({'id':stage_id,'status':stage.get('status'),'round':stage.get('round'),
                       'head':stage.get('head'),'body':stage.get('body'),
                       'checkpoints':stage.get('checkpoints',[])})
    actors=state.get('plan',{}).get('actors',[])
    if isinstance(actors,dict):actors=[{'public_key':k,**v} if isinstance(v,dict) else {'public_key':k,'role':v} for k,v in actors.items()]
    result={'schema':'hypertrix.public-read-replica/1','accepted_at':datetime.now(timezone.utc).isoformat(),
            'header_time':certificate['signed_header']['header']['time'],'height':height,'app_hash':digest,
            'chain_id':state['chain_id'],'genesis_sha256':GENESIS_PIN,'verification':receipt,
            'stages':stages,'actors':actors,'training_members':state.get('training_members',{}),'validator_count':4,
            'network':public_network(state,json.loads(genesis.read_bytes())),
            'scope':'Quorum-certified local protocol-4 ledger. Admission/receipts are ledger facts, not live process or GPU telemetry. One host/operator. Older hybrid chain is separate.',
            'certificate_sha256':hashlib.sha256(raw).hexdigest(),
            'certificate':'live/certificate.json','genesis':'live/genesis.json'}
    activity={'target_tokens_per_second':None,'accepted_bytes_per_second':None,'flops_per_second':None}
    if previous and previous.get('network'):
        seconds=(datetime.fromisoformat(result['header_time'].replace('Z','+00:00'))-datetime.fromisoformat(previous['header_time'].replace('Z','+00:00'))).total_seconds()
        if 0<seconds<=300:
            for target,source in [('target_tokens_per_second','completed_target_tokens'),('accepted_bytes_per_second','accepted_artifact_bytes')]:
                difference=result['network'][source]-previous['network'].get(source,0)
                if difference>=0:activity[target]=difference/seconds
            activity['sample_seconds']=seconds
    result['activity']=activity
    return result,raw,genesis.read_bytes()


if __name__=='__main__':
    root=ROOT/'artifacts/public-site';snapshot,raw,genesis=capture(root)
    (root/'status.json').write_bytes(canonical_json(snapshot));(root/'certificate.json').write_bytes(raw)
    print(json.dumps({'verified':True,'height':snapshot['height'],'chain_id':snapshot['chain_id']}))
