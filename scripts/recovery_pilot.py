"""Fresh protocol-6 live GPU recovery trial. Run helpers inside the pinned image.

The shell driver stops a real trainer after submission and withholds one named
verifier. No synthetic updates, fabricated replay approvals or reduced quorum.
"""
import argparse
import asyncio
import base64
import copy
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src')); sys.path.insert(0, str(ROOT/'scripts'))
from dawl_node.common import read_json, write_json, keygen, load_key, canonical_json, parse_json, sign
from dawl_profiles.workspace import Workspace
from dawl_profiles.profiles import content_id
from dawl_platform.workflow import implementation_digest
from dawl_cluster.client import Client
from dawl_cluster.training_protocol import round_jobs
from dawl_training.work import catalog, model_contract
from dawl_swarm.dht import atomic
import oracle_training_pilot as old
import profile_testnet

NODES = ('trainer-0', 'trainer-1', 'trainer-2', 'verifier-0', 'verifier-1', 'verifier-2')
PROVIDER = '127.70.0.28'


def envelope(body): return {'id': content_id(body), 'body': body}
def public(root, name): return old.public(root, name)
def stage_id(root, label): return read_json(root/f'stage-{label}.json')['id']


def index(root):
    objects = Workspace(root/'provider').objects
    atomic(root/'provider/artifact-index.json', {p.name: {'sha256': p.name, 'bytes': p.stat().st_size}
           for p in objects.root.iterdir() if len(p.name) == 64})


def prepare(root):
    from dawl_cluster.contributions import endpoint
    from dawl_swarm.dht import route
    endpoint(f'http://{PROVIDER}:8900'); route(f'http://{PROVIDER}:8903',8903)
    for i in range(6): route(f'http://127.70.0.{61+i}:8903',8903)
    old.prepare(root, Path('/repo'), Path('/source'), Path('/profile-source'))
    # Rewrite fresh configuration only; historical networks and keys are untouched.
    for path in list(root.rglob('cluster.json'))+list(root.rglob('config.toml')):
        text = path.read_text().replace('127.72.', '127.74.').replace('127.70.0.27', PROVIDER)
        for i in range(5): text = text.replace(f'127.70.0.{40+i}', f'127.70.0.{61+i}')
        path.write_text(text)
    node = root/'verifier-2'; keygen(node/'key.private.pem', node/'key.public.hex')
    for filename in ('plan.json', 'genesis.json', 'profile-capabilities.json'):
        shutil.copyfile(root/'verifier-1'/filename, node/filename)
    config = read_json(root/'verifier-1/cluster.json')
    config.update(training_host='127.70.0.66', training_name='verifier-2')
    write_json(node/'cluster.json', config)
    print('Fresh recovery namespace: validators 127.74, provider 127.70.0.28, workers .61–66', flush=True)


async def activate(root, client):
    from dawl_training.environment import capture
    env = capture('cuda:0'); write_json(root/'environment.json', env)
    manifest = read_json(root/'manifest.json'); pid = manifest['profile']['id']
    await client.command('activate_training', {'version': 4, 'implementation_sha256': implementation_digest(),
                                             'runtime_manifest': content_id(env['body']['runtime'])})
    for name in NODES: await old.admit(root, client, name)
    await client.command('activate_collaboration', {'version': 5, 'implementation_sha256': implementation_digest()})
    body = {'schema': 'hypertrix.collaboration-manifest/1', 'name': 'Recovery live GPU cohort',
            'mode': 'homogeneous', 'profiles': [pid], 'members': sorted(public(root,n) for n in NODES),
            'environments': [env], 'verification': 'full-replay-and-reduce/1', 'admission': 'explicit-members/1'}
    collaboration = envelope(body); write_json(root/'collaboration.json', collaboration)
    await client.command('register_collaboration', {'manifest': collaboration})
    for name in NODES:
        member = Client(root/name, load_key(root/name/'key.private.pem'))
        try: await member.command('accept_collaboration', {'collaboration': collaboration['id'], 'environment': env['id']})
        finally: await member.close()
    state = await client.state()
    branch = envelope({'schema': 'hypertrix.model-branch/1', 'name': 'Recovery trial',
        'collaboration': collaboration['id'], 'environment': env['id'], 'profile_id': pid,
        'token_blueprint': state['profile_heads'][pid]['id'], 'parent': None,
        'parent_model': manifest['parent_model']['sha256'], 'model_contract': model_contract(manifest),
        'initial_adapter': manifest['initial_adapter']})
    await client.command('register_model_branch', {'branch': branch}); write_json(root/'branch.json', branch)
    await client.command('activate_training_recovery', {'version': 6, 'implementation_sha256': implementation_digest()})
    print('Activated protocols 4/5/6 with measured GPU cohort and six accepted members.', flush=True)


async def stage(root, client, label):
    state = await client.state(); branch = state['model_branches'][read_json(root/'branch.json')['id']]
    manifest = copy.deepcopy(read_json(root/'manifest.json'))
    manifest['initial_adapter'] = branch['adapter']
    manifest['runtime'] = read_json(root/'environment.json')['body']['runtime']
    manifest['recipe'].update(rounds=1, local_steps=2)
    ws = Workspace(root/'provider')
    if label != 'a':
        # Copy only checked public adapter bytes; this is an explicit replica.
        desc = branch['adapter']
        previous = 'a' if label == 'b' else 'b'
        source = Workspace(root/'runs'/previous/'verifier-0/public').objects
        if ws.objects.put(source.read(desc)) != desc: raise ValueError('parent copy differs')
    desc = ws.objects.json(manifest); write_json(root/f'manifest-{label}.json', manifest)
    body = {'schema': 'hypertrix.oracle-training-stage/3',
        **{k: branch['body'][k] for k in ('profile_id','token_blueprint','parent_model','model_contract')},
        'branch': branch['id'], 'parent_checkpoint': branch['head'], 'manifest': desc,
        'initial_adapter': branch['adapter'], 'rounds': 1, 'shards': 2, 'local_steps': 2,
        'lease_blocks': 120, 'work': catalog(ws.objects.read_json(manifest['dataset']),1,2,2),
        'verifiers': sorted(public(root,n) for n in ('verifier-0', 'verifier-2' if label == 'b' else 'verifier-1')),
        'recovery': {'round_timeout_blocks': 300 if label == 'b' else 1200,
                     'max_rejections': 3, 'supersedes': stage_id(root,'b') if label == 'c' else None}}
    value = envelope(body)
    await client.command('register_training_stage', {'stage': value}); write_json(root/f'stage-{label}.json',value)
    for name in NODES:
        node = root/'runs'/label/name; node.mkdir(parents=True)
        for filename in ('key.private.pem','key.public.hex','plan.json','genesis.json','profile-capabilities.json'):
            shutil.copyfile(root/name/filename,node/filename)
        config = read_json(root/name/'cluster.json')
        config.update(training_stage=value['id'], collaboration_manifest=branch['body']['collaboration'],
                      execution_environment=branch['body']['environment'])
        write_json(node/'cluster.json',config)
    index(root)
    print(f'Registered stage {label}: {value["id"]}',flush=True)


async def wait_for(root, client, condition, label, seconds=600):
    start = time.monotonic(); last = None
    while time.monotonic()-start < seconds:
        state = await client.state(); s = state['training_stages'][stage_id(root,label)]; jobs = round_jobs(state,s)
        status = (s['status'], sum(bool(j['contribution']) for _,j in jobs), len(s['votes']), len(s['failure_reports']))
        if status != last: print(f'{label}: status, updates, votes, reports = {status}',flush=True); last=status
        if s['status'] == 'disputed': raise ValueError('unexpected disputed stage')
        matched = {
            'submitted': any(j['contribution'] and j['contribution']['signer'] == public(root,'trainer-0') for _,j in jobs),
            'failure': bool(s['failure_reports']),
            'vote': len(s['votes']) == 1,
            'complete': s['status'] == 'complete',
            'deadline': state['height'] >= s['round_started_height']+s['body']['recovery']['round_timeout_blocks'],
        }[condition]
        if matched:
            write_json(root/f'observation-{label}-{condition}-{state["height"]}.json',
                       {'height':state['height'], 'stage':s, 'jobs':dict(jobs)})
            return state,s,jobs
        await asyncio.sleep(2)
    raise TimeoutError(f'{label} did not reach {condition} within {seconds}s')


async def reject(root, client):
    state,s,jobs = await wait_for(root,client,'failure','a')
    report_id,report = next(iter(s['failure_reports'].items()))
    if report['reason'] != 'artifact_unavailable': raise ValueError('expected actual withholding failure')
    if state['training_jobs'][report['job']]['contribution']['signer'] != public(root,'trainer-0'):
        raise ValueError('wrong withheld contribution')
    await client.command('reject_training_update', {'stage':s['id'],'round':0,'revision':0,'report':report_id})
    print('Owner rejected exact unavailable contribution; replacement lease now allowed.',flush=True)


async def cancel(root, client):
    await wait_for(root,client,'vote','b')
    state,s,jobs = await wait_for(root,client,'deadline','b')
    if len(s['votes']) != 1 or s['checkpoints']: raise ValueError('missing-verifier scenario invalid')
    await client.command('cancel_training_stage', {'stage':s['id'],'round':0,'revision':0,'reason':'round_timeout'})
    after = await client.state(); branch = after['model_branches'][s['body']['branch']]
    if branch['head'] != s['head'] or branch['active_stage'] is not None: raise ValueError('cancellation advanced branch')
    # Submit a correctly signed late approval from the absent verifier. CheckTx
    # rejection is captured, then reproduced against this certified state offline.
    candidate = next(iter(s['votes'].values()))['candidate']; public_key=public(root,'verifier-2')
    body = {'schema':'dawl.cluster-tx/1','chain_id':after['chain_id'],'plan_sha256':after['plan_sha256'],
        'sequence':after['actors'][public_key]['sequence']+1,'valid_until':after['height']+120,
        'kind':'verify_training_round','payload':{'stage':s['id'],'candidate':candidate}}
    tx=sign(body,load_key(root/'verifier-2/key.private.pem'))
    rejection=await client.rpc('broadcast_tx_sync',{'tx':base64.b64encode(canonical_json(tx)).decode()})
    if rejection['code'] == 0: raise ValueError('late cancelled-stage vote accepted')
    write_json(root/'late-vote.json',{'height':after['height'],'transaction':tx,'rpc_rejection':rejection})
    print('Timed-out stage cancelled without promotion; late verifier vote rejected.',flush=True)


async def export(root, client):
    state,s,jobs = await wait_for(root,client,'complete','c')
    certificate = await client.certificate(client.config['rpcs'][0],state['height']); client.accept_certificate(certificate)
    state=certificate['state']; target=root.parent/'evidence'; target.mkdir()
    write_json(target/'training-certificate.json',certificate)
    blocks={}
    for first in range(1,state['height']+1,8):
        heights=list(range(first,min(first+8,state['height']+1)))
        values=await asyncio.gather(*(client.rpc('block',{'height':str(h)},client.config['rpcs'][0]) for h in heights))
        for h,value in zip(heights,values):
            txs=[parse_json(base64.b64decode(t,validate=True)) for t in value['block']['data']['txs'] or []]
            if txs: blocks[str(h)]=txs
    write_json(target/'application-history.json',{'height':state['height'],'blocks':blocks})
    for name in ['genesis.json','environment.json','collaboration.json','branch.json','late-vote.json',
                 'certificate.json','public-evidence.json']:
        shutil.copyfile(root/name,target/name)
    for pattern in ('stage-?.json','manifest-?.json','observation-*.json'):
        for path in root.glob(pattern): shutil.copyfile(path,target/path.name)
    for path in (root/'runs').rglob('*.json'):
        if path.parent.name == 'public':
            destination=target/'workers'/path.relative_to(root/'runs'); destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,destination)
    for path in (root/'runs').rglob('events.jsonl'):
        destination=target/'workers'/path.relative_to(root/'runs'); destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,destination)
    objects=Workspace(target).objects
    for s in state['training_stages'].values():
        for report in s['failure_reports'].values():
            name=next(n for n in NODES if public(root,n)==report['signer'])
            source=root/'runs'/'a'/name/'public/objects'/report['evidence']
            if objects.put(source.read_bytes())['sha256'] != report['evidence']: raise ValueError('failure evidence hash differs')
    shutil.copytree(ROOT/'src',target/'src',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (target/'scripts').mkdir()
    for name in ('recovery_pilot.py','verify_recovery_pilot.py'):
        shutil.copyfile(ROOT/'scripts'/name,target/'scripts'/name)
    write_json(target/'trial.json',{'schema':'hypertrix.training-recovery-pilot/1',
        'completed_utc':datetime.now(timezone.utc).isoformat(),'genesis_sha256':client.config['genesis_sha256'],
        'implementation_sha256':implementation_digest(),'identities':{n:public(root,n) for n in NODES},
        'scope':'One operator, one RTX 5090 Laptop GPU; separate containers, keys and CAS. Real training and replay, four CometBFT validators.'})
    from verify_recovery_pilot import verify
    result=verify(target,client.config['genesis_sha256']); write_json(target/'verification.json',result)
    print(canonical_json(result).decode(),flush=True)


async def command(root, cmd, label):
    client=Client(root/'publisher',load_key(root/'publisher/key.private.pem'))
    try:
        if cmd=='activate': await activate(root,client)
        elif cmd=='stage': await stage(root,client,label)
        elif cmd=='reject': await reject(root,client)
        elif cmd=='cancel': await cancel(root,client)
        elif cmd=='export': await export(root,client)
        else: await wait_for(root,client,cmd,label)
    finally: await client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('command')
    p.add_argument('--root',type=Path,default=Path('/pilot')); p.add_argument('--stage',choices=['a','b','c'],default='a')
    a=p.parse_args()
    if a.command=='prepare': prepare(a.root)
    elif a.command=='bootstrap': asyncio.run(profile_testnet.bootstrap(a.root,f'http://{PROVIDER}:8900'))
    elif a.command=='source-finish': asyncio.run(profile_testnet.finish(a.root))
    elif a.command=='source-evidence': asyncio.run(profile_testnet.evidence(a.root))
    elif a.command=='dht':
        import uvicorn
        from dawl_swarm.dht import DHT
        c=read_json(a.root/'cluster.json')
        d=DHT(a.root,c['network_sha256'],'recovery-cortex',PROVIDER,['PROVIDER','TOKENIZER'],[])
        uvicorn.run(d.app(),host=PROVIDER,port=8903,access_log=False)
    else: asyncio.run(command(a.root,a.command,a.stage))


if __name__=='__main__': main()
