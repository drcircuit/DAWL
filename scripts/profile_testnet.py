"""Reproducible isolated protocol-3 pilot, run inside the local container image.

Preparation imports PUBLIC corpus artifacts and signed profile ledgers only. New
testnet identities are generated; no live validator identity is copied. The tiny
v1/v2 bootstrap is explicitly synthetic; all v3 jobs replay the real 13-book corpus.
"""
import argparse
import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, '/app/src')
from dawl_node.common import canonical_json, keygen, load_key, read_json, write_json, sha256
from dawl_platform.workflow import implementation_digest
from dawl_profiles.workspace import Workspace, ProfileLedger
from dawl_profiles.profiles import ProfileTokenizer, content_id
from dawl_profiles.bundles import export_bundle, export_index, import_bundle
from dawl_cluster.prepare import comet_identity
from dawl_cluster.client import Client
from dawl_cluster.state import initial
from dawl_cluster.profile_protocol import summary, done


def prepare(root, repository, public_cache=None):
    root.mkdir(parents=True, exist_ok=False)
    public = {}
    for name in ('publisher', 'worker-0', 'worker-1', 'provider'):
        directory = root/name; directory.mkdir()
        public[name] = keygen(directory/'key.private.pem', directory/'key.public.hex')
    spec = dict(kind='full-shard-replay/v1', vectors_entry='a'*64, book_id=1, positions=[0], tokens=1, dense_bytes=4)
    recipe = implementation_digest()
    plan = dict(schema='dawl.cluster-plan/1', chain_id='hypertrix-profiles-'+str(int(time.time())), replicas=2, lease_blocks=120,
        actors={public[name]: {'name': name, 'hats': ['PUBLISHER'] if name == 'publisher' else ['AUDIT_CPU']}
                for name in ('publisher','worker-0','worker-1')},
        books=[dict(book_id=1, vectors_entry='a'*64, chunks=1, tokens=1)], jobs=[dict(id=content_id(spec), spec=spec)],
        embedding_dimension=1, implementation_sha256=recipe, lineage_checkpoint={'set_sha256': 'c'*64},
        training_manifest={'sha256': 'd'*64, 'bytes': 1}, imported_weight={'id': 'e'*64, 'body': {'kind': 'test-fixture', 'training_history_verified': False}},
        scope='Synthetic one-token v1/v2 bootstrap solely for testing the protocol upgrade. V3 uses actual signed corpus artifacts.')
    initial(plan)
    validators = [comet_identity() for _ in range(4)]; peers = [comet_identity() for _ in range(4)]
    genesis = dict(genesis_time=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), chain_id=plan['chain_id'], initial_height='1',
        consensus_params={'block': {'max_bytes': '1048576', 'max_gas': '-1'},
            'evidence': {'max_age_num_blocks': '100000', 'max_age_duration': '172800000000000', 'max_bytes': '1048576'},
            'validator': {'pub_key_types': ['ed25519']}, 'version': {'app': '1'}, 'abci': {'vote_extensions_enable_height': '0'}},
        validators=[{'address': k['address'], 'pub_key': k['pub_key'], 'power': '10', 'name': 'oracle-'+str(i)} for i,k in enumerate(validators)],
        app_hash='', app_state=plan)
    config = {'rpcs': ['http://127.71.0.'+str(10+i)+':26657' for i in range(4)], 'providers': [],
              'plan_sha256': content_id(plan), 'genesis_sha256': content_id(genesis),
              'verify_state': True, 'max_certificate_age_seconds': 120, 'network_sha256': content_id(genesis),
              'profile_dht': 'http://127.70.0.27:8903'}
    for name in list(public)+['validator-'+str(i) for i in range(4)]:
        directory=root/name; directory.mkdir(exist_ok=True)
        write_json(directory/'plan.json', plan); write_json(directory/'genesis.json', genesis)
        if name in ('provider', 'publisher'): write_json(directory/'cluster.json', config)
    write_json(root/'plan.json', plan); write_json(root/'genesis.json', genesis)
    for i in range(4):
        directory=root/('validator-'+str(i)); comet=directory/'comet'
        (comet/'config').mkdir(parents=True); (comet/'data').mkdir()
        write_json(comet/'config/genesis.json', genesis)
        write_json(comet/'config/priv_validator_key.json', validators[i])
        write_json(comet/'config/node_key.json', {'priv_key': peers[i]['priv_key']})
        write_json(comet/'data/priv_validator_state.json', {'height':'0','round':0,'step':0})
        address='127.71.0.'+str(10+i)
        write_json(directory/'cluster.json', {**config, 'abci_host': address, 'abci_unix': True})
        persistent=','.join(f"{key['address'].lower()}@127.71.0.{10+j}:26656" for j,key in enumerate(peers) if j != i)
        (comet/'config/config.toml').write_text(f'''proxy_app = "unix:///node/sockets/abci.sock"
moniker = "profile-oracle-{i}"
log_level = "error"
db_backend = "goleveldb"
db_dir = "data"
abci = "socket"
[rpc]
laddr = "tcp://{address}:26657"
max_open_connections = 128
max_body_bytes = 1048576
unsafe = false
[p2p]
laddr = "tcp://{address}:26656"
external_address = "{address}:26656"
persistent_peers = "{persistent}"
addr_book_strict = false
allow_duplicate_ip = true
pex = false
[mempool]
size = 1000
max_txs_bytes = 33554432
max_tx_bytes = 65536
[consensus]
timeout_propose = "2s"
timeout_prevote = "1s"
timeout_precommit = "1s"
timeout_commit = "500ms"
create_empty_blocks = true
create_empty_blocks_interval = "500ms"
''', encoding='utf-8')
    ws=Workspace(root/'provider'); proposals=[]
    inputs=[] if public_cache else [('multiprofile-pilot-001','multiprofile-run-001'), ('o200k-pilot-001','o200k-run-001')]
    if public_cache:
        for obj in (public_cache/'objects').iterdir(): shutil.copyfile(obj,ws.objects.root/obj.name)
        for proposal in read_json(public_cache/'proposals.json'):
            envelope=ws.objects.read_json(proposal['bundle']); profile=envelope['body']['profile']
            print('Revalidating cached '+profile['body']['label'],flush=True)
            ledger=import_bundle(root/'imported'/profile['id'],envelope,{'provider':envelope['body']['signer'],**proposal},profile)
            tokenizer=ProfileTokenizer(profile,ws.objects.read(profile['body']['tokenizer']['artifact']),
                ws.objects.read(profile['body']['model']['config']) if profile['body']['model'] else None)
            proposals.append(export_bundle(ws,ledger,tokenizer,ledger.get(proposal['catalog']['blueprint']),load_key(root/'provider/key.private.pem')))
    for folder, report in inputs:
        source=repository/'artifacts'/folder; results=read_json(repository/'docs/results'/report/'report.json')
        # Copy this public CAS once, rather than rescanning it for each profile.
        for obj in (source/'objects').iterdir():
            destination=ws.objects.root/obj.name
            if not destination.exists(): shutil.copyfile(obj,destination)
        for item in results['profiles']:
            profile=item['profile']; pid=profile['id']; old=source/'profiles'/pid
            print('Preparing '+profile['body']['label'], flush=True)
            ledger=ProfileLedger(old/'ledger.sqlite', item['trust'], profile, read_only=True)
            tokenizer=ProfileTokenizer(profile, ws.objects.read(profile['body']['tokenizer']['artifact']),
                ws.objects.read(profile['body']['model']['config']) if profile['body']['model'] else None)
            proposal=export_bundle(ws,ledger,tokenizer,item['blueprint'],load_key(root/'provider/key.private.pem'))
            proposals.append(proposal)
    write_json(root/'proposals.json', proposals)
    write_json(root/'provider/artifact-index.json', export_index(ws, proposals))
    ids=sorted(p['profile_id'] for p in proposals)
    write_json(root/'provider/profile-capabilities.json', ids)
    for name in ('worker-0','worker-1'):
        write_json(root/name/'cluster.json', {**config, 'supported_profiles': ids})
        write_json(root/name/'profile-capabilities.json', ids)
    write_json(root/'activation.json', {'version':3,'implementation_sha256':recipe,'runtime_manifest':content_id({'image':'hypertrix-profiles:v3','implementation':recipe})})
    print('Prepared six profile namespaces; fresh validators and worker identities.', flush=True)


async def bootstrap(root):
    clients={name:Client(root/name,load_key(root/name/'key.private.pem')) for name in ('publisher','provider','worker-0','worker-1')}
    try:
        publisher=clients['publisher']; recipe=implementation_digest()
        # Exercise old transitions with a labelled fixture, never report these as real vector audits.
        for name in ('worker-0','worker-1'):
            client=clients[name]; lease=await client.command('claim',{'capability':'AUDIT_CPU'})
            await client.command('result',{'job':lease['job'],'generation':lease['generation'],'report':{
                'outcome':'pass','checked_chunks':1,'tokens_checked':1,'dense_bytes':4,'implementation_sha256':recipe,'execution_ms':0,'error':''}})
        await publisher.command('publish_dataset',{})
        await publisher.command('activate_contributions',dict(version=2,implementation_sha256=recipe,runtime_manifest='f'*64,tokenizer_sha256='1'*64,embedding_sha256='2'*64))
        await clients['provider'].command('announce_provider', {'name':'profile-cortex','endpoint':'http://127.70.0.27:8900','capabilities':['PROVIDER','TOKENIZER']})
        await publisher.command('activate_profiles',read_json(root/'activation.json'))
        ws=Workspace(root/'provider')
        for proposal in read_json(root/'proposals.json'):
            profile=ws.objects.read_json(proposal['bundle'])['body']['profile']
            await publisher.command('register_profile',{'profile':profile})
            await clients['provider'].command('contribute_profile',proposal)
            print('Committed profile '+profile['body']['label'],flush=True)
        write_json(root/'bootstrap-state.json',await publisher.state())
    finally:
        for client in clients.values(): await client.close()


async def finish(root):
    client=Client(root/'publisher',load_key(root/'publisher/key.private.pem'))
    try:
        deadline=time.monotonic()+1800; last=None
        while time.monotonic()<deadline:
            state=await client.state(); status=summary(state)
            counts={v['label']:v['complete'] for v in status.values()}
            if counts!=last: print(counts,flush=True); last=counts
            for pid, value in status.items():
                if value['jobs'] and value['complete']==value['jobs'] and value['blueprint'] is None:
                    await client.command('publish_profile', {'profile_id':pid})
            if all(value['blueprint'] for value in status.values()): break
            invalid=[j for j in state['profile_jobs'].values() if any(f['outcome']=='invalid' for f in j['failures'])]
            if invalid: raise ValueError('a profile job is disputed')
            await asyncio.sleep(3)
        else: raise TimeoutError('profile corpus audit timed out')
        # All public clients verify the actual CometBFT quorum against fresh genesis.
        certificate=await client.certificate(client.config['rpcs'][0],state['height'])
        client.accept_certificate(certificate); state=certificate['state']
        write_json(root/'certificate.json',certificate)
        profiles=[]
        for pid, item in summary(state).items():
            books=item['blueprint']['body']['books']
            profiles.append({'profile_id':pid,'label':item['label'],'books':len(books),'frags':sum(b['frags'] for b in books),
                'tokens':sum(b['tokens'] for b in books),'jobs':item['jobs'],'complete':item['complete'],'blueprint':item['blueprint']['id'],
                'receipts':len(item['blueprint']['body']['receipts'])})
        report={'schema':'hypertrix.profile-testnet/1','completed_utc':datetime.now(timezone.utc).isoformat(),
            'chain_id':state['chain_id'],'height':state['height'],'app_hash':sha256(canonical_json(state)),
            'implementation_sha256':implementation_digest(),'validators':4,'auditors':2,'profiles':profiles,
            'profile_jobs':len(state['profile_jobs']),'receipts':sum(len(j['attestations']) for j in state['profile_jobs'].values()),
            'quorum_certificate_verified':certificate['verified'],'genesis_sha256':client.config['genesis_sha256'],
            'legacy_fixture_preserved':state['profile_protocol']['legacy_release']==state['dataset_release']['id'],
            'scope':'One local host; separate containers, genuine CometBFT quorum and HTTP/DHT artifact replay. Two auditor keys, one operator. Synthetic v1/v2 bootstrap only. V3 uses 13 real books per profile. No vector/model/training claim.'}
        write_json(root/'report.json',report); print(canonical_json(report).decode(),flush=True)
    finally: await client.close()


async def evidence(root):
    """Export signatures and public lineage; never private key or signing state."""
    certificate=read_json(root/'certificate.json'); state=certificate['state']
    wanted={a['transaction'] for j in state['profile_jobs'].values() for a in j['attestations'].values()}
    heights=sorted({a['height'] for j in state['profile_jobs'].values() for a in j['attestations'].values()})
    client=Client(root/'publisher'); transactions={}
    try:
        for first in range(0,len(heights),4):
            blocks=await asyncio.gather(*(client.rpc('block',{'height':str(h)},client.config['rpcs'][0]) for h in heights[first:first+4]))
            for block in blocks:
                for encoded in block['block']['data']['txs'] or []:
                    from dawl_node.common import parse_json
                    tx=parse_json(base64.b64decode(encoded,validate=True))
                    if tx['id'] in wanted: transactions[tx['id']]=tx
        if set(transactions)!=wanted: raise ValueError('missing accepted audit transaction')
        ws=Workspace(root/'provider')
        write_json(root/'public-evidence.json',{'genesis':read_json(root/'genesis.json'),
            'audit_transactions':[transactions[i] for i in sorted(transactions)],
            'bundles':[ws.objects.read_json(p['bundle']) for p in read_json(root/'proposals.json')]})
        print('Exported '+str(len(transactions))+' signed audit transactions and six public lineage bundles.',flush=True)
    finally: await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('command',choices=['prepare','bootstrap','finish','evidence','dht'])
    parser.add_argument('--root',type=Path,default=Path('/pilot')); parser.add_argument('--repository',type=Path,default=Path('/repo'))
    parser.add_argument('--public-cache',type=Path)
    args=parser.parse_args()
    if args.command=='prepare': prepare(args.root,args.repository,args.public_cache)
    elif args.command=='dht':
        import uvicorn
        from dawl_swarm.dht import DHT
        config=read_json(args.root/'cluster.json')
        dht=DHT(args.root,config['network_sha256'],'profile-cortex','127.70.0.27',['PROVIDER','TOKENIZER'],[])
        uvicorn.run(dht.app(),host='127.70.0.27',port=8903,access_log=False)
    elif args.command=='evidence': asyncio.run(evidence(args.root))
    else: asyncio.run(bootstrap(args.root) if args.command=='bootstrap' else finish(args.root))


if __name__=='__main__':main()
