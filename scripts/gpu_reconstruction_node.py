"""Independent CUDA reconstruction node for the invited Hypertrix pilot.

Discovers providers, verifies fresh Oracle state and signed lineage, replays all
released books, and signs a diagnostic report. It never claims execution leases,
submits consensus transactions, trains, or persists dense sequence vectors.
"""
import argparse
import asyncio
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import httpx
import numpy as np
from cuda_gather import CudaGather
from contributor_node import bootstrap
from container_node import resources
from dawl_cluster.client import Client
from dawl_cluster.contribution_artifacts import import_manifest, catalog
from dawl_node.common import canonical_json, load_key, read_json, sha256, sign
from dawl_platform.artifacts import Objects, PublicPeer
from dawl_platform.auditor import verify_book
from dawl_platform.ledger import Ledger
from dawl_platform.vectors import EmbeddingTable
from dawl_platform.workflow import implementation_digest
from dawl_swarm.dht import atomic, validate
from dawl_swarm.node import lineage_files


def assert_equal(actual, expected):
    if actual != expected:
        raise ValueError('GPU output differs byte-for-byte from independent NumPy lookup')


def self_test():
    # Non-square table, partial block, repeated IDs, first/last IDs and raw fp32 bits.
    bits = np.array([0, 0x80000000, 1, 0x80000001, 0x3f800000, 0xbf800000, 0x7f7fffff], dtype='<u4')
    matrix = np.tile(bits.view('<f4'), (7, 1))
    matrix[2] = np.arange(7, dtype='<f4')
    gpu = CudaGather(matrix)
    try:
        for ids in ([6, 0, 2, 2, 1], [0], [6] * 16384):
            actual = gpu.payload(ids)
            assert_equal(actual, matrix[ids].tobytes())
        bad = bytes([actual[0] ^ 1]) + actual[1:]
        try: assert_equal(bad, matrix[ids].tobytes())
        except ValueError: pass
        else: raise ValueError('corruption canary was not rejected')
        for ids in ([], [-1], [7], [0.0], [[0]], [0] * 16385):
            try: gpu.payload(ids)
            except ValueError: pass
            else: raise ValueError('invalid GPU indices were accepted')
        return dict(accepted=True, equality_cases=3, rejected_index_cases=6,
                    flipped_bit_rejected=True, runtime=gpu.runtime, launches=gpu.calls)
    finally: gpu.close()


class ComparedTable:
    def __init__(self, path, descriptor, cancelled):
        self.cpu = EmbeddingTable(path, descriptor, 'cpu')
        self.gpu = CudaGather(self.cpu.matrix)
        self.runtime = {**self.gpu.runtime, 'reference': self.cpu.runtime}
        self.cancelled = cancelled
        self.cpu_seconds = 0.0

    def payload(self, ids):
        if self.cancelled(): raise InterruptedError('GPU node deadline or shutdown reached')
        actual = self.gpu.payload(ids)
        start = time.perf_counter()
        expected = self.cpu.payload(ids)
        self.cpu_seconds += time.perf_counter() - start
        assert_equal(actual, expected)
        return actual


class Sources:
    """Fallback among validated providers; Objects still checks hash and size."""
    def __init__(self, urls):
        if not urls: raise OSError('No signed live provider record for released inputs')
        self.peers = [PublicPeer(url, True) for url in sorted(set(urls))]
        self.received = 0

    def request(self, route, limit):
        failures = []
        for peer in self.peers:
            try:
                result = peer.request(route, limit)
                # Fail over on corrupt objects as well as transport failures.
                if sha256(result) != route.rsplit('/', 1)[-1] or len(result) != limit:
                    raise ValueError('provider object differs from commitment')
                self.received += len(result)
                return result
            except (OSError, ValueError) as error: failures.append(str(error)[:120])
        raise OSError('No provider supplied a valid object: ' + '; '.join(failures))


def replay(root, state, peers, progress, cancelled):
    started = time.perf_counter()
    release = state['dataset_release']
    if not release or release['id'] != sha256(canonical_json(release['body'])):
        raise ValueError('certified state has no valid Blueprint')
    body = release['body']
    if body['schema'] not in ('dawl.dataset-release/1', 'dawl.dataset-release/2'):
        raise ValueError('unsupported Blueprint')
    config = read_json(root / 'cluster.json')
    baseline = Ledger(root / 'baseline/ledger.sqlite', read_json(root / 'trust.json'))
    for entry in read_json(root / 'lineage.json'): baseline.add(entry)
    if baseline.verify_history() != state['plan']['lineage_checkpoint']:
        raise ValueError('bootstrap lineage differs from certified plan')
    active = state.get('protocol_upgrade', {}).get('implementation_sha256', state['plan']['implementation_sha256'])
    if implementation_digest() != active:
        raise ValueError('node source differs from active runtime pin')
    objects = Objects(root / 'objects')
    base_urls = [p['provider'] for p in peers if p['provider'] in config['providers']]
    baseline_peer = Sources(base_urls)
    sources = [(baseline, baseline_peer, state['plan']['books'])]
    manifests = [state['plan']['training_manifest']]
    for identity in body.get('contributions', []):
        contribution = state['contributions'][identity]
        committed_url = state['providers'][contribution['provider']]['endpoint']
        peer = Sources([p['provider'] for p in peers
                        if p['signer'] == contribution['provider'] and p['provider'] == committed_url])
        objects.fetch(peer, contribution['manifest'])
        ledger = import_manifest(root / 'contributions' / identity, objects.read_json(contribution['manifest']),
                                 contribution, baseline.trust['anchors'])
        for descriptor in lineage_files(ledger).values():
            if cancelled(): raise InterruptedError('GPU node stopped while fetching')
            objects.fetch(peer, descriptor)
        if catalog(ledger, objects) != contribution['catalog']:
            raise ValueError('contribution catalog does not reproduce signed artifacts')
        sources.append((ledger, peer, contribution['catalog']['books']))
        manifests.append(contribution['catalog']['training_manifest'])
    books = [book for _, _, items in sources for book in items]
    if (books != body['books'] or len({b['book_id'] for b in books}) != len(books)
            or body.get('training_manifests', [state['plan']['training_manifest']]) != manifests):
        raise ValueError('replay coverage differs from certified Blueprint')
    tables, by_digest, reports = {}, {}, []
    try:
        for ledger, peer, items in sources:
            for book in items:
                if cancelled(): raise InterruptedError('GPU node stopped before book replay')
                progress.update(activity='reconstructing', current_book=book['book_id'])
                vector = ledger.get(book['vectors_entry'])
                if vector['body']['payload']['tokens_entry'] != book['tokens_entry']:
                    raise ValueError('released tokens differ from vector lineage')
                table_id = vector['body']['payload']['table_entry']
                resource = ledger.get(table_id)['body']['payload']
                descriptor = resource['descriptor']
                if descriptor['sha256'] not in by_digest:
                    path = objects.fetch(peer, resource['files']['table'])
                    by_digest[descriptor['sha256']] = ComparedTable(path, descriptor, cancelled)
                table = by_digest[descriptor['sha256']]
                if table.cpu.descriptor != descriptor: raise ValueError('table descriptor changed')
                tables[table_id] = table
                result = verify_book(ledger, objects, peer, vector, tables)
                if result['checked_chunks'] != book['chunks'] or result['tokens'] != book['tokens']:
                    raise ValueError('verified coverage differs from released book')
                reports.append(result)
                progress.update(books_checked=len(reports), chunks_checked=sum(r['checked_chunks'] for r in reports),
                                tokens_checked=sum(r['tokens'] for r in reports))
                print(f"GPU verified book {book['book_id']}: {result['checked_chunks']} Frags", flush=True)
        chunks = sum(r['checked_chunks'] for r in reports)
        if chunks < 1 or sum(t.gpu.calls for t in by_digest.values()) != chunks:
            raise ValueError('GPU launch coverage differs from Frag coverage')
        return dict(schema='hypertrix.gpu-reconstruction/1', accepted=True, dataset_release=release['id'],
            certified_height=state['height'], network_sha256=config['network_sha256'],
            genesis_sha256=config['genesis_sha256'], books=len(reports), chunks=chunks,
            tokens=sum(r['tokens'] for r in reports), dense_bytes=sum(r['dense_bytes_reconstructed'] for r in reports),
            seconds=time.perf_counter()-started, received_bytes=sum(p.received for _, p, _ in sources),
            cuda_launches=sum(t.gpu.calls for t in by_digest.values()),
            cuda_event_kernel_ms=sum(t.gpu.kernel_ms for t in by_digest.values()),
            cuda_roundtrip_seconds=sum(t.gpu.roundtrip_seconds for t in by_digest.values()),
            numpy_reference_seconds=sum(t.cpu_seconds for t in by_digest.values()),
            runtimes=[t.runtime for t in by_digest.values()], results=reports,
            persisted_dense_vector_bytes=0, training_executed=False, consensus_receipt=False,
            scope='Full released source/token lineage replay, GPU bytes == NumPy bytes == committed vector SHA-256. '
                  'Self-signed diagnostic; no audit admission or proof of remote computation. '
                  'CUDA event time excludes transfers; end-to-end time includes downloads and source verification.')
    finally:
        for table in by_digest.values(): table.gpu.close()


PAGE = b'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Hypertrix GPU Daemon</title>
<style>body{background:#0c1420;color:#dce6f4;font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}pre{white-space:pre-wrap;word-break:break-word;background:#122136;padding:20px;border-radius:12px}a{color:#79d8bf}</style>
<h1>GPU Daemon \xc2\xb7 Vector reconstruction</h1><p>Independent CUDA replay of the certified Blueprint. No training or consensus votes.</p>
<p><a href="http://127.0.0.1:8882/">Hypertrix Console</a> \xc2\xb7 <a href="/api/report">Signed diagnostic report</a></p>
<pre id="status">Connecting...</pre><script>async function tick(){try{let r=await fetch('/api/status');if(!r.ok)throw Error('Status unavailable');document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.getElementById('status').textContent=e.message}}tick();setInterval(tick,3000)</script></html>'''


async def run(args):
    root = args.root
    bootstrap(root, args.bundle, args.network)
    key = load_key(root / 'key.private.pem')
    public = key.public_key().public_bytes_raw().hex()
    deadline = datetime.fromisoformat(args.deadline)
    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM): signal.signal(sig, lambda *_: stopping.set())
    cancelled = lambda: stopping.is_set() or datetime.now(timezone.utc) >= deadline
    if cancelled(): raise ValueError('pilot deadline has passed')
    progress = dict(activity='starting', books_checked=0, chunks_checked=0, tokens_checked=0)
    status = {}
    scripts = {p: sha256((Path(__file__).parent / p).read_bytes())
               for p in ('gpu_reconstruction_node.py', 'cuda_gather.py')}
    def snapshot():
        value = dict(name=args.name, role='gpu-reconstructor', site='local', host=args.host, phase='running',
            observed_at=datetime.now(timezone.utc).isoformat(), public_key=public, capabilities=['AUDITOR'],
            admitted_auditor=False, image=os.environ.get('DAWL_IMAGE_PIN'), resources=resources(),
            network_sha256=args.network, deadline=args.deadline, diagnostic=dict(progress), counters={}, events=[])
        if (root/'dht-status.json').exists(): value['dht'] = read_json(root/'dht-status.json')
        status.clear(); status.update(value)
        atomic(root/'status.json', value)
        atomic(args.telemetry/(args.name+'.json'), value)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            if self.path == '/': data, mime = PAGE, 'text/html; charset=utf-8'
            elif self.path == '/api/status': data, mime = canonical_json(dict(status)), 'application/json'
            elif self.path == '/api/report' and (root/'last-report.json').exists():
                data, mime = (root/'last-report.json').read_bytes(), 'application/json'
            else: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    peer = subprocess.Popen([sys.executable, str(Path(__file__).parent/'peer_node.py'), '--root', str(root),
        '--name', args.name, '--host', args.host, '--roles', 'AUDITOR', '--seeds', json.dumps(args.seeds)])
    client = Client(root)
    args.telemetry.mkdir(parents=True, exist_ok=True)
    async def heartbeat():
        while not cancelled():
            snapshot()
            await asyncio.sleep(3)
    beat = asyncio.create_task(heartbeat())
    completed = None
    try:
        check = await asyncio.to_thread(self_test)
        atomic(root/'gpu-self-test.json', check)
        progress['device'] = check['runtime']['device']
        while not cancelled():
            if peer.poll() is not None: raise RuntimeError('DHT peer exited')
            try:
                state = await client.state()
                progress['certified_height'] = state['height']
                release = state['dataset_release']
                if release and release['id'] != completed:
                    progress.update(activity='discovering', dataset_release=release['id'], error=None)
                    async with httpx.AsyncClient(timeout=40, trust_env=False) as http:
                        response = await http.get('http://'+args.host+':8903/dht/roles/PROVIDER')
                        response.raise_for_status()
                    peers = [validate(r, args.network) for r in response.json()['records']]
                    atomic(root/'input-state.json', state)
                    atomic(root/'discovered-providers.json', response.json())
                    progress.update(activity='fetching', books_checked=0, chunks_checked=0, tokens_checked=0)
                    result = await asyncio.to_thread(replay, root, state, peers, progress, cancelled)
                    result.update(observed_at=datetime.now(timezone.utc).isoformat(), scripts=scripts,
                                  input_state_sha256=sha256(canonical_json(state)), self_test=check)
                    report = sign(result, key)
                    (root/'reports').mkdir(exist_ok=True)
                    atomic(root/'reports'/(report['id']+'.json'), report)
                    atomic(root/'last-report.json', report)
                    completed = release['id']
                    progress.update(activity='watching Blueprint', report_id=report['id'], accepted=True,
                        cuda_launches=result['cuda_launches'], dense_bytes=result['dense_bytes'], seconds=result['seconds'],
                        cuda_event_kernel_ms=result['cuda_event_kernel_ms'])
                    print(json.dumps({k: result[k] for k in ('dataset_release','books','chunks','tokens','dense_bytes','seconds')}), flush=True)
                elif completed: progress['activity'] = 'watching Blueprint'
            except (OSError, ValueError, RuntimeError, httpx.HTTPError, KeyError) as error:
                progress.update(activity='retry pending', error=str(error)[:300])
                print('GPU node: '+str(error), flush=True)
            for _ in range(30):
                if cancelled(): break
                await asyncio.sleep(1)
    finally:
        stopping.set(); beat.cancel(); await asyncio.gather(beat, return_exceptions=True)
        await client.close(); server.shutdown(); server.server_close()
        if peer.poll() is None: peer.terminate()
        try: peer.wait(timeout=10)
        except subprocess.TimeoutExpired: peer.kill(); peer.wait()
        snapshot(); status.update(phase='stopped')
        atomic(root/'status.json', status); atomic(args.telemetry/(args.name+'.json'), status)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/node'))
    parser.add_argument('--bundle', type=Path, default=Path('/bootstrap'))
    parser.add_argument('--telemetry', type=Path, default=Path('/telemetry'))
    parser.add_argument('--name', default='gpu-0'); parser.add_argument('--host', default='127.70.0.23')
    parser.add_argument('--port', type=int, default=8883)
    parser.add_argument('--network', required=True); parser.add_argument('--deadline', required=True)
    parser.add_argument('--seeds', nargs='+', default=['http://127.70.0.21:8903','http://127.70.0.20:8903'])
    asyncio.run(run(parser.parse_args()))
