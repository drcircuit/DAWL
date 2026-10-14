"""Fresh local Oracle + real corpus + leased GPU training pilot. No cloud calls."""
import argparse
import asyncio
import base64
import copy
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src')); sys.path.insert(0, str(ROOT/'scripts'))
from dawl_node.common import keygen, load_key, read_json, write_json, sign, parse_json, canonical_json
from dawl_profiles.profiles import content_id
from dawl_profiles.workspace import Workspace
from dawl_platform.workflow import implementation_digest
from dawl_cluster.client import Client
from dawl_training.work import catalog
from dawl_swarm.dht import atomic
import profile_testnet

NODES = ('trainer-0', 'trainer-1', 'trainer-2', 'verifier-0', 'verifier-1')


def prepare(root, repo, source, profile_source):
    if root.exists(): raise ValueError('fresh pilot root required')
    old = read_json(source/'stage.json')['body']; pid = old['profile']['id']
    cache = root.parent/'cache'; cache.mkdir()
    (cache/'objects').symlink_to(source/'objects', target_is_directory=True)
    write_json(cache/'proposals.json', [p for p in read_json(profile_source/'proposals.json') if p['profile_id'] == pid])
    profile_testnet.prepare(root, repo, cache)
    # All of these identities are fresh and have never signed a block.
    plan = read_json(root/'plan.json'); plan['chain_id'] = 'hypertrix-training-'+str(int(time.time()))
    plan['scope'] = 'Synthetic one-token v1/v2 upgrade bootstrap only. Actual 13-book SmolLM2 token replay and training follow.'
    genesis = read_json(root/'genesis.json'); genesis['chain_id'] = plan['chain_id']; genesis['app_state'] = plan
    config = read_json(root/'publisher/cluster.json')
    config.update(plan_sha256=content_id(plan), genesis_sha256=content_id(genesis), network_sha256=content_id(genesis),
                  rpcs=[f'http://127.72.0.{10+i}:26657' for i in range(4)])
    for directory in [root]+[p for p in root.iterdir() if p.is_dir() and (p/'plan.json').exists()]:
        atomic(directory/'plan.json', plan); atomic(directory/'genesis.json', genesis)
        path = directory/'cluster.json'
        if path.exists():
            value = read_json(path); value.update(config)
            if 'abci_host' in value: value['abci_host'] = value['abci_host'].replace('127.71.', '127.72.')
            atomic(path, value)
        if (directory/'comet').exists():
            atomic(directory/'comet/config/genesis.json', genesis)
            path = directory/'comet/config/config.toml'
            path.write_text(path.read_text().replace('127.71.', '127.72.').replace('profile-oracle-', 'training-oracle-'))
    for i, name in enumerate(NODES):
        node = root/name; keygen(node/'key.private.pem', node/'key.public.hex')
        write_json(node/'plan.json', plan); write_json(node/'genesis.json', genesis)
        write_json(node/'profile-capabilities.json', [pid])
        write_json(node/'cluster.json', {**config, 'training_host': f'127.70.0.{40+i}',
                   'training_name': name, 'training_profile': pid, 'training_seeds': ['http://127.70.0.27:8903']})
    manifest = {k: copy.deepcopy(old[k]) for k in ('profile', 'parent_model', 'dataset', 'adapter_layout',
                'initial_adapter', 'projection_layout', 'fixed_projection', 'recipe', 'runtime', 'upstream')}
    manifest['schema'] = 'hypertrix.training-manifest/2'
    manifest['recipe'].update(algorithm='fixed-A-LoRA/local-SGD/leased-token-weighted-mean/v1', rounds=4, local_steps=2)
    ws = Workspace(root/'provider'); desc = ws.objects.json(manifest)
    write_json(root/'manifest.json', manifest); write_json(root/'manifest-descriptor.json', desc)
    atomic(root/'provider/artifact-index.json', {p.name: {'sha256': p.name, 'bytes': p.stat().st_size}
               for p in ws.objects.root.iterdir() if len(p.name) == 64})
    print('Prepared fresh 4-validator Oracle, 13-book profile, 3 trainers and 2 verifiers.', flush=True)


def public(root, name): return (root/name/'key.public.hex').read_text().strip()


async def admit(root, publisher, name):
    config = read_json(root/name/'cluster.json')
    return await publisher.command('admit_training_worker', {'public': public(root, name),
        'role': 'TRAINER' if name.startswith('trainer') else 'VERIFIER', 'profiles': [config['training_profile']]})


async def stage(root):
    client = Client(root/'publisher', load_key(root/'publisher/key.private.pem'))
    try:
        state = await client.state(); manifest = read_json(root/'manifest.json'); pid = manifest['profile']['id']
        await client.command('activate_training', {'version': 4, 'implementation_sha256': implementation_digest(),
            'runtime_manifest': content_id(manifest['runtime'])})
        for name in NODES:
            if name != 'trainer-2': await admit(root, client, name)
        ws = Workspace(root/'provider'); dataset = ws.objects.read_json(manifest['dataset'])
        b = {'schema': 'hypertrix.oracle-training-stage/1', 'profile_id': pid,
             'token_blueprint': state['profile_heads'][pid]['id'], 'manifest': read_json(root/'manifest-descriptor.json'),
             'initial_adapter': manifest['initial_adapter'], 'parent_model': manifest['parent_model']['sha256'],
             'rounds': manifest['recipe']['rounds'], 'shards': 2, 'local_steps': manifest['recipe']['local_steps'],
             'lease_blocks': 60, 'work': catalog(dataset, manifest['recipe']['rounds'], 2, manifest['recipe']['local_steps']),
             'verifiers': sorted(public(root, n) for n in ('verifier-0', 'verifier-1'))}
        value = {'id': content_id(b), 'body': b}
        await client.command('register_training_stage', {'stage': value})
        write_json(root/'stage.json', value)
        for name in NODES:
            path = root/name/'cluster.json'; config = read_json(path); config['training_stage'] = value['id']; atomic(path, config)
        print('Registered stage '+value['id'], flush=True)
    finally: await client.close()


async def replace(root):
    client = Client(root/'publisher', load_key(root/'publisher/key.private.pem'))
    try:
        abandoned = read_json(root/'trainer-0/abandoned.json'); lease = abandoned['lease']; old = public(root, 'trainer-0')
        deadline = time.monotonic()+180
        while time.monotonic() < deadline:
            state = await client.state(); job = state['training_jobs'][lease['job']]
            if old not in job['leases']: break
            await asyncio.sleep(2)
        else: raise TimeoutError('abandoned lease did not expire')
        if job['contribution']: raise ValueError('abandoned work unexpectedly accepted')
        # Broadcast a correctly signed result with expired generation; CheckTx must reject.
        manifest = read_json(root/'manifest.json')
        body = {'schema': 'dawl.cluster-tx/1', 'chain_id': state['chain_id'], 'plan_sha256': state['plan_sha256'],
                'sequence': state['actors'][old]['sequence']+1, 'valid_until': state['height']+120,
                'kind': 'result_training', 'payload': {**{k: lease[k] for k in ('stage','job','generation')},
                    'parent': abandoned['spec']['parent'], 'adapter': manifest['initial_adapter']}}
        tx = sign(body, load_key(root/'trainer-0/key.private.pem'))
        rejected = await client.rpc('broadcast_tx_sync', {'tx': base64.b64encode(canonical_json(tx)).decode()})
        if rejected['code'] == 0: raise ValueError('expired training receipt passed CheckTx')
        # RPC rejection is diagnostic, not itself a quorum certificate. Persist both.
        state = await client.state()
        if state['training_jobs'][lease['job']]['contribution'] or state['actors'][old]['sequence'] != body['sequence']-1:
            raise ValueError('expired contribution changed certified state')
        await admit(root, client, 'trainer-2')
        write_json(root/'churn.json', {'abandoned': abandoned, 'expired_height': state['height'],
            'late_transaction': tx, 'rpc_rejection': rejected, 'certified_actor_unchanged': True,
            'replacement': public(root, 'trainer-2')})
        print('Expired lease fenced; newly admitted replacement may now join.', flush=True)
    finally: await client.close()


async def finish(root):
    client = Client(root/'publisher'); stage_id = read_json(root/'stage.json')['id']; deadline = time.monotonic()+900; last = None
    try:
        while time.monotonic() < deadline:
            state = await client.state(); s = state['training_stages'][stage_id]
            if (s['round'],s['status']) != last:
                last = (s['round'],s['status']); print({'round':s['round'],'status':s['status']}, flush=True)
            if s['status'] == 'disputed': raise ValueError('stage disputed')
            if s['status'] == 'complete': break
            await asyncio.sleep(2)
        else: raise TimeoutError('training stage incomplete')
        cert = await client.certificate(client.config['rpcs'][0], state['height']); client.accept_certificate(cert)
        write_json(root/'training-certificate.json', cert); state = cert['state']; s = state['training_stages'][stage_id]
        # Collect original signed on-chain transactions, not reconstructed envelopes.
        heights = {state['training_protocol']['height'], s['height']}
        heights.update(m['height'] for m in state['training_members'].values())
        heights.update(j['contribution']['height'] for j in state['training_jobs'].values() if j['contribution'])
        heights.update(v['height'] for c in state['training_checkpoints'].values() for v in c['receipts'].values())
        transactions = {}
        for height in sorted(heights):
            block = await client.rpc('block', {'height': str(height)}, client.config['rpcs'][0])
            for encoded in block['block']['data']['txs'] or []:
                tx = parse_json(base64.b64decode(encoded, validate=True)); transactions[tx['id']] = tx
        write_json(root/'training-transactions.json', transactions)
        # Reconstruct every application transition, including claims/expiry and
        # rejected block transactions. Empty blocks are implicit in the height.
        nonempty = {}
        for first in range(1, state['height']+1, 8):
            heights_batch = list(range(first, min(first+8, state['height']+1)))
            blocks = await asyncio.gather(*(client.rpc('block', {'height':str(h)}, client.config['rpcs'][0]) for h in heights_batch))
            for h, block in zip(heights_batch, blocks):
                txs = [parse_json(base64.b64decode(t, validate=True)) for t in block['block']['data']['txs'] or []]
                if txs: nonempty[str(h)] = txs
        write_json(root/'application-history.json', {'height':state['height'],'blocks':nonempty})
        jobs = list(state['training_jobs'].values()); churn = read_json(root/'churn.json')
        replacement = state['training_jobs'][churn['abandoned']['lease']['job']]
        if replacement['contribution']['signer'] != churn['replacement'] or replacement['attempts'] != 2:
            raise ValueError('replacement did not finish exact abandoned shard')
        report = {'schema':'hypertrix.oracle-training-pilot/1','chain_id':state['chain_id'],'stage':stage_id,
                  'checkpoint':s['head'],'rounds':s['round'],'updates':len(jobs),
                  'training_targets':sum(j['spec']['target_tokens'] for j in jobs),
                  'training_sequences':sum(j['spec']['sequences'] for j in jobs),
                  'verifier_replays_required':len(jobs)*len(s['body']['verifiers']),
                  'distinct_successful_trainers':len({j['contribution']['signer'] for j in jobs}),
                  'replacement_completed_same_shard':True, 'expired_leases':state['expired_leases'],
                  'source_books':len(state['profile_heads'][s['body']['profile_id']]['body']['books']),
                  'source_receipts':len(state['profile_heads'][s['body']['profile_id']]['body']['receipts']),
                  'genesis_sha256':client.config['genesis_sha256'],'implementation_sha256':implementation_digest(),
                  'scope':'One local host/GPU, separate containers and keys. Four real CometBFT validators. Full replay attestations by two admitted verifier keys; no proof of remote computation, WAN scaling or independent operators.'}
        write_json(root/'training-report.json', report); print(report, flush=True)
    finally: await client.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('command', choices=['prepare','bootstrap','source-finish','stage','replace','finish','evidence'])
    p.add_argument('--root', type=Path, default=Path('/pilot')); a = p.parse_args()
    if a.command == 'prepare': prepare(a.root, Path('/repo'), Path('/source'), Path('/profile-source'))
    elif a.command == 'bootstrap': asyncio.run(profile_testnet.bootstrap(a.root))
    elif a.command == 'source-finish': asyncio.run(profile_testnet.finish(a.root))
    elif a.command == 'evidence': asyncio.run(profile_testnet.evidence(a.root))
    else: asyncio.run({'stage':stage,'replace':replace,'finish':finish}[a.command](a.root))
