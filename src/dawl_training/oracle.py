"""Lease-driven training worker and independent replay/replication verifier.

All decisions come from quorum-certified state. DHT records discover replicas,
never grant authority. Each process owns its key, CAS, outbox and model state.
"""
import argparse
import asyncio
import contextlib
from importlib.metadata import version
from pathlib import Path
import signal
import time
import httpx
import uvicorn
from dawl_node.common import load_key, read_json, write_json, canonical_json
from dawl_cluster.client import Client
from dawl_cluster.training_protocol import round_jobs, candidate
from dawl_platform.workflow import implementation_digest
from dawl_platform.artifacts import PublicPeer
from dawl_profiles.workspace import Workspace
from dawl_profiles.bundles import import_bundle, descriptors
from dawl_profiles.profiles import ProfileTokenizer, describe
from dawl_swarm.dht import DHT, validate as validate_peer
from .network import Network, serve
from .data import make_dataset
from .work import validate_manifest, records, reduce_updates


def lineage(ws, state, stage, network):
    pid = stage['body']['profile_id']; profile = state['profiles'][pid]
    head = next(h for h in state['profile_releases'][pid] if h['id'] == stage['body']['token_blueprint'])
    entries = []; books = []
    for batch_id in head['body']['contributions']:
        batch = state['profile_contributions'][batch_id]
        network.fetch(batch['bundle']); envelope = ws.objects.read_json(batch['bundle'])
        ledger = import_bundle(ws.root/'lineage'/batch_id, envelope, batch, profile)
        for desc in descriptors(envelope).values(): network.fetch(desc)
        for entry in ledger.entries():
            if entry['body']['kind'] == 'tokens':
                manifest = ws.objects.read_json(entry['body']['payload']['data']['manifest'])
                for desc in descriptors(manifest).values(): network.fetch(desc)
        tokenizer = ProfileTokenizer(profile, ws.objects.read(profile['body']['tokenizer']['artifact']),
                                     ws.objects.read(profile['body']['model']['config']))
        ws.verify_blueprint(ledger, tokenizer, ledger.get(batch['catalog']['blueprint']))
        entries.extend(ledger.get(b['tokens_entry']) for b in batch['catalog']['books'])
        books.extend(batch['catalog']['books'])
    if sorted(books, key=lambda b: b['book_id']) != head['body']['books']: raise ValueError('certified corpus coverage differs')
    return make_dataset(ws.objects, entries)


async def discover(client, network, state):
    config = client.config; allowed = set(state['providers']) | set(state['training_members'])
    urls = []
    # Own DHT node performs iterative lookup; bootstrap is not a scheduler.
    async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
        response = await http.get('http://'+config['training_host']+':8903/dht/roles/PROVIDER')
        response.raise_for_status()
        if len(response.content) > 256*1024: raise ValueError('discovery response too large')
        for record in response.json()['records']:
            body = validate_peer(record, config['network_sha256'])
            if body['signer'] in allowed and config['training_profile'] in body.get('profiles', []):
                urls.append(body['provider'])
    if not urls: raise OSError('no admitted live artifact replicas discovered')
    network.peers = [PublicPeer(u, True) for u in sorted(set(urls))]


def bootstrap(root, state, stage, ws, network, device):
    from .model import assemble, load_model, restore
    import torch
    protocol = state['collaboration_protocol'] if 'branch' in stage['body'] else state['training_protocol']
    if protocol['implementation_sha256'] != implementation_digest():
        raise ValueError('worker implementation not activated')
    network.fetch(stage['body']['manifest']); manifest = ws.objects.read_json(stage['body']['manifest'])
    if manifest['profile'] != state['profiles'][stage['body']['profile_id']]:
        raise ValueError('executable profile differs from certified profile')
    dataset = lineage(ws, state, stage, network)
    network.fetch(manifest['dataset'])
    if ws.objects.read_json(manifest['dataset']) != dataset: raise ValueError('dataset differs from verified lineage')
    validate_manifest(manifest, stage, dataset)
    for desc in manifest['parent_model']['chunks']+[manifest['fixed_projection'], manifest['initial_adapter']]: network.fetch(desc)
    if device.startswith('cuda') and not torch.cuda.is_available(): raise ValueError('CUDA required; fallback disabled')
    checkpoint = assemble(ws.objects, manifest['parent_model'], root/'model/model.safetensors')
    model = load_model(ws.objects.read(manifest['profile']['body']['model']['config']), checkpoint)
    restore(model, ws.objects.read(manifest['fixed_projection']), manifest['projection_layout'], 'A')
    model.to(device)
    return manifest, dataset, model


def log(root, event, **fields):
    value = {'event': event, **fields}
    with (root/'events.jsonl').open('ab') as stream: stream.write(canonical_json(value)+b'\n')
    print(canonical_json(value).decode(), flush=True)


async def work(root, role, device, abandon=False):
    from .model import restore, train_local, export
    root = Path(root); key = load_key(root/'key.private.pem'); client = Client(root, key)
    config = client.config; ws = Workspace(root/'public'); network = Network(ws.objects, [], root/'transfers.jsonl')
    role_name = 'TRAINER' if role == 'trainer' else 'TRAINING_VERIFIER'
    dht = DHT(root, config['network_sha256'], config['training_name'], config['training_host'],
              ['PROVIDER', role_name], config['training_seeds'])
    dht_server = uvicorn.Server(uvicorn.Config(dht.app(), host=config['training_host'], port=8903, log_level='warning'))
    # Caller owns signals so tensor work can finish before process shutdown.
    dht_server.capture_signals = contextlib.nullcontext
    dht_task = asyncio.create_task(dht_server.serve()); server = serve(root/'public', config['training_host'], 8900)
    stop = asyncio.Event(); loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, stop.set)
    try:
        while not dht_server.started:
            if dht_task.done(): await dht_task; raise RuntimeError('DHT did not start')
            await asyncio.sleep(.1)
        state = await client.state(); stage = state['training_stages'][config['training_stage']]
        environment_runtime = None
        if 'branch' in stage['body']:
            from .environment import capture, check_stage
            measured = await asyncio.to_thread(capture, device)
            environment_runtime = check_stage(state, stage, client.public, config, measured)
        await discover(client, network, state)
        manifest, dataset, model = await asyncio.to_thread(bootstrap, root, state, stage, ws, network, device)
        if environment_runtime is not None and manifest['runtime'] != environment_runtime:
            raise ValueError('executable runtime differs from agreed execution cohort')
        log(root, 'ready', role=role, stage=stage['id'], public=client.public)
        while not stop.is_set():
            state = await client.state(); stage = state['training_stages'][config['training_stage']]
            if stage['status'] == 'disputed': raise ValueError('Oracle stage disputed; operator investigation required')
            if stage['status'] == 'complete':
                await discover(client, network, state)
                await asyncio.to_thread(network.fetch, stage['adapter'])
                write_json(root/'public/final.json', {'stage': stage['id'], 'head': stage['head'], 'adapter': stage['adapter']})
                log(root, 'complete', head=stage['head'], rounds=stage['round'])
                await stop.wait(); break
            jobs = round_jobs(state, stage)
            if role == 'trainer':
                eligible = not any(j['contribution'] and j['contribution']['signer'] == client.public for _, j in jobs)
                if not eligible or not any(not j['contribution'] and (not j['leases'] or client.public in j['leases']) for _, j in jobs):
                    await asyncio.sleep(1); continue
                lease = await client.command('claim_training', {'stage': stage['id']})
                if not lease['job']: continue
                state = await client.state(); job = state['training_jobs'][lease['job']]
                fields = {k: lease[k] for k in ('stage', 'job', 'generation')}
                log(root, 'claimed', **lease, spec=job['spec'])
                if abandon:
                    write_json(root/'abandoned.json', {'lease': lease, 'spec': job['spec']})
                    return 75
                async def renew():
                    while True:
                        await asyncio.sleep(2)
                        await client.command('renew_training', fields)
                heartbeat = asyncio.create_task(renew())
                try:
                    await discover(client, network, state)
                    await asyncio.to_thread(network.fetch, stage['adapter'])
                    def train():
                        restore(model, ws.objects.read(stage['adapter']), manifest['adapter_layout'])
                        started = time.perf_counter()
                        loss = train_local(model, records(dataset, job['spec']), ws.objects, manifest['recipe'], device)
                        layout, payload = export(model)
                        if layout != manifest['adapter_layout']: raise ValueError('changed adapter layout')
                        return ws.objects.put(payload), loss, time.perf_counter()-started
                    adapter, loss, seconds = await asyncio.to_thread(train)
                    payload = {**fields, 'parent': stage['head'], 'adapter': adapter}
                    write_json(root/'public'/('work-'+lease['job']+'-'+lease['generation']+'.json'), payload)
                finally:
                    heartbeat.cancel(); await asyncio.gather(heartbeat, return_exceptions=True)
                result = await client.command('result_training', payload)
                log(root, 'contributed', **result, adapter=adapter, seconds=seconds, loss=loss)
            else:
                if client.public in stage['votes'] or any(not j['contribution'] for _, j in jobs):
                    await asyncio.sleep(1); continue
                await discover(client, network, state)
                def replay():
                    network.fetch(stage['adapter']); checks = []
                    for job_id, job in jobs:
                        network.fetch(job['contribution']['adapter'])
                        restore(model, ws.objects.read(stage['adapter']), manifest['adapter_layout'])
                        train_local(model, records(dataset, job['spec']), ws.objects, manifest['recipe'], device)
                        _, payload = export(model)
                        if describe(payload) != job['contribution']['adapter']:
                            raise ValueError('full numerical training replay differs')
                        checks.append({'job': job_id, 'update': job['contribution']['transaction'], 'bit_exact': True})
                    adapter = ws.objects.put(reduce_updates(ws.objects, stage['adapter'], [j for _, j in jobs]))
                    return candidate(state, stage, adapter), checks
                body, checks = await asyncio.to_thread(replay)
                # Persist evidence and serve all referenced artifacts BEFORE attesting.
                write_json(root/'public'/f"replay-{stage['round']:03d}.json", {'candidate': body, 'checks': checks})
                result = await client.command('verify_training_round', {'stage': stage['id'], 'candidate': body})
                log(root, 'verified', round=body['round'], checks=checks, **result)
        return 0
    finally:
        dht_server.should_exit = True; await dht_task
        server.shutdown(); server.server_close(); await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['trainer', 'verifier']); parser.add_argument('--root', type=Path, default=Path('/node'))
    parser.add_argument('--device', default='cuda:0'); parser.add_argument('--abandon-after-claim', action='store_true')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(work(args.root, args.role, args.device, args.abandon_after_claim)))


if __name__ == '__main__': main()
