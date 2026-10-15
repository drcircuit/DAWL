"""Profile-aware Daemon: discover, pull, verify lineage, retokenize, attest."""
import asyncio
import os
from pathlib import Path
from time import perf_counter
from importlib.metadata import version
import httpx
from dawl_node.common import load_key, sha256
from dawl_platform.workflow import implementation_digest
from dawl_profiles.workspace import Workspace
from dawl_profiles.profiles import ProfileTokenizer
from dawl_profiles.bundles import import_bundle, descriptors
from dawl_swarm.transfer import Puller, Unavailable
from dawl_swarm.dht import validate as validate_peer
from .client import Client
from .profile_protocol import done
from .telemetry import TransferTelemetry


async def replay(root, state, job_id, providers, transfer, telemetry):
    job = state['profile_jobs'][job_id]; spec = job['spec']; pid = spec['profile_id']
    profile = state['profiles'][pid]; batch = state['profile_contributions'][spec['contribution']]
    ws = Workspace(root); puller = Puller(transfer, ws.objects, telemetry, providers)
    async def fetch(items):
        # Bounded fanout, shared whole-object hashes, with existing piece validation.
        for first in range(0, len(items), 4):
            await asyncio.gather(*(puller.fetch(d, providers) for d in items[first:first+4] if d['bytes']))
        for d in items:
            if not d['bytes'] and ws.objects.put(b'') != d: raise ValueError('invalid empty artifact')
    await fetch([batch['bundle']])
    ledger = await asyncio.to_thread(import_bundle, Path(root)/'bundles'/batch['bundle']['sha256'],
                                    ws.objects.read_json(batch['bundle']), batch, profile)
    entry = ledger.get(spec['tokens_entry']); data = entry['body']['payload']['data']
    source = ledger.get(data['source_entry'])['body']['payload']['data']
    if source['artifact']['sha256'] != spec['source_sha256'] or source['artifact']['bytes'] > 8*1024*1024:
        raise ValueError('source differs from job')
    if data['manifest']['bytes'] > 2*1024*1024: raise ValueError('token manifest exceeds bound')
    await fetch(list(descriptors(profile).values())+[source['artifact'], data['manifest']])
    manifest = ws.objects.read_json(data['manifest'])
    records = manifest['records']
    if not isinstance(records, list) or len(records) != spec['frags']: raise ValueError('manifest coverage differs from job')
    needed = list(descriptors(records).values())
    if len(needed) > 3*2050 or sum(d['bytes'] for d in needed) > 64*1024*1024:
        raise ValueError('Frag artifacts exceed bounded book transfer')
    await fetch(needed)
    tokenizer = await asyncio.to_thread(ProfileTokenizer, profile, ws.objects.read(profile['body']['tokenizer']['artifact']),
        ws.objects.read(profile['body']['model']['config']) if profile['body']['model'] else None)
    result = await asyncio.to_thread(ws.verify_tokens, ledger, tokenizer, entry)
    if result['frags'] != spec['frags'] or result['tokens'] != spec['tokens'] or result['book_id'] != spec['book_id']:
        raise ValueError('replay differs from certified job')
    return result


async def discover(client, transfer, state, job):
    pid = job['spec']['profile_id']; batch = state['profile_contributions'][job['spec']['contribution']]
    endpoint = state['providers'][batch['provider']]['endpoint']
    dht = client.config.get('profile_dht') or ('http://'+os.environ['DAWL_HOST']+':8903' if os.environ.get('DAWL_HOST') else None)
    if dht:
        probe = Puller(transfer, Workspace(client.root/'profile-cache').objects, client, [dht])
        data = await probe.json(dht, '/dht/roles/PROVIDER')
        for record in data['records']:
            peer = validate_peer(record, client.config['network_sha256'])
            if peer['signer'] == batch['provider'] and peer['provider'] == endpoint and pid in peer.get('profiles', []):
                return [endpoint]
        raise Unavailable('no live signed provider record for this profile')
    # Explicit local bootstrap for test rigs. No fetched data can supply a new URL.
    if endpoint not in client.config.get('profile_providers', []): raise Unavailable('profile provider not in local bootstrap')
    return [endpoint]


async def attempt(root, once=False):
    root = Path(root); client = Client(root, load_key(root/'key.private.pem'))
    implementation = implementation_digest()
    configured = sorted(set(client.config['supported_profiles']))
    if not configured: raise ValueError('profile worker needs explicit supported profiles')
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as transfer:
        try:
            while True:
                state = await client.state()
                if state.get('profile_protocol', {}).get('implementation_sha256') != implementation:
                    if once: raise ValueError('worker runtime is not activated for protocol 3')
                    await asyncio.sleep(5); continue
                supported = [pid for pid in configured if pid in state['profiles'] and
                    state['profiles'][pid]['body']['tokenizer']['version'] == version(state['profiles'][pid]['body']['tokenizer']['library'])]
                eligible = any(j['spec']['profile_id'] in supported and (client.public in j['leases'] or
                    (not done(state, j) and client.public not in j['attestations'] and not any(f['outcome'] == 'invalid' for f in j['failures'])
                     and len(j['leases'])+len(j['attestations']) < state['plan']['replicas'])) for j in state['profile_jobs'].values())
                if not eligible:
                    if once: return
                    await asyncio.sleep(3); continue
                lease = await client.command('claim_profile', {'profile_ids': supported})
                if lease['job'] is None:
                    if once: return
                    continue
                identity = {k: lease[k] for k in ('profile_id', 'job', 'generation')}
                async def renew():
                    while True:
                        await asyncio.sleep(3)
                        await client.command('renew_profile', identity)
                heartbeat = asyncio.create_task(renew()); started = perf_counter()
                report = dict(outcome='error', checked_chunks=0, tokens_checked=0,
                              implementation_sha256=implementation, execution_ms=0, error='')
                try:
                    state = await client.state(); job = state['profile_jobs'][lease['job']]
                    providers = await discover(client, transfer, state, job)
                    telemetry = TransferTelemetry(client)
                    try:
                        result = await replay(root/'profile-cache', state, lease['job'], providers, transfer, telemetry)
                    finally: telemetry.flush()
                    report.update(outcome='pass', checked_chunks=result['frags'], tokens_checked=result['tokens'])
                except (Unavailable, httpx.HTTPError) as error: report.update(outcome='unavailable', error=str(error)[:512])
                except ValueError as error: report.update(outcome='invalid', error=str(error)[:512])
                except (OSError, RuntimeError, KeyError, TypeError) as error: report.update(outcome='error', error=str(error)[:512])
                finally:
                    heartbeat.cancel(); await asyncio.gather(heartbeat, return_exceptions=True)
                report['execution_ms'] = int((perf_counter()-started)*1000)
                result = await client.command('result_profile', {**identity, 'report': report})
                client.event('profile_audit', profile_id=lease['profile_id'], job=lease['job'], report=report, result=result)
                print(f"Profile {lease['profile_id'][:12]} job {lease['job'][:12]}: {report['outcome']}", flush=True)
                if report['outcome'] in ('unavailable', 'error'):
                    if once: raise RuntimeError(report['error'])
                    await asyncio.sleep(5)
        finally: await client.close()


async def run(root, once=False):
    while True:
        try:
            return await attempt(root, once)
        except (OSError, ValueError, KeyError, httpx.HTTPError) as error:
            if once: raise
            print(f'Profile worker reconnecting: {str(error)[:300]}', flush=True)
            await asyncio.sleep(5)
