"""Separate CPU commitment and cold HTTP/CUDA replay over certified token Blueprints.

This diagnostic does not submit vector receipts to consensus or alter token heads.
Each profile runs in its own process to bound tokenizer/driver allocator lifetimes.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from dawl_node.common import (canonical_json, keygen, load_key, read_json, sha256,
                              sign, unpack_ids, verify_signature, write_json)
from dawl_platform.artifacts import Objects, PublicPeer
from dawl_platform.workflow import implementation_digest
from dawl_profiles.bundles import import_bundle, descriptors
from dawl_profiles.profiles import ProfileTokenizer, content_id, describe, bind_o200k_model
from dawl_profiles.o200k_weights import verify_compatibility, verify_corpus_compatibility
from dawl_profiles.workspace import Workspace
from dawl_profiles.vectors import (CONTRACT, import_embedding, materialize,
                                   validate_commitment, verify_frag)
from cuda_gather import CudaGather

GENESIS = '8a3f05580926f5ca9d0c5c87f4c7e9334a93d9e1e3145cb2eb2be624a6097dc6'
NAMES = ('gpt2-baseline', 'smollm2', 'qwen3', 'qwen35', 'smollm3', 'o200k_base')


def utc(): return datetime.now(timezone.utc).isoformat()


def certificate(evidence):
    result = subprocess.run(['/usr/local/bin/check-certificate', '-genesis', str(evidence/'genesis.json'),
        '-genesis-sha256', GENESIS, '-certificate', str(evidence/'completed-certificate.json')],
        check=True, capture_output=True, text=True)
    return read_json(evidence/'completed-certificate.json')['state'], json.loads(result.stdout)


def select(state, name):
    profiles = [p for p in state['profiles'].values() if p['body']['label'] == name]
    if len(profiles) != 1: raise ValueError('ambiguous profile')
    profile = profiles[0]; pid = profile['id']; head = state['profile_heads'][pid]
    batches = [b for b in state['profile_contributions'].values() if b['profile_id'] == pid]
    if len(batches) != 1 or content_id(head['body']) != head['id']: raise ValueError('unsupported pilot coverage')
    return profile, head, batches[0]


def lineage(ws, state, name, fetch):
    profile, head, batch = select(state, name)
    fetch(batch['bundle']); envelope = ws.objects.read_json(batch['bundle'])
    ledger = import_bundle(ws.root/'lineage'/profile['id'], envelope, batch, profile)
    for desc in descriptors(envelope).values(): fetch(desc)
    for entry in ledger.entries():
        if entry['body']['kind'] == 'tokens':
            manifest = ws.objects.read_json(entry['body']['payload']['data']['manifest'])
            for desc in descriptors(manifest).values(): fetch(desc)
    tokenizer = ProfileTokenizer(profile, ws.objects.read(profile['body']['tokenizer']['artifact']),
                                  ws.objects.read(profile['body']['model']['config']) if profile['body']['model'] else None)
    blueprint = ledger.get(batch['catalog']['blueprint'])
    replay = ws.verify_blueprint(ledger, tokenizer, blueprint)
    # Certificate head binds this same catalog and therefore exact book/token-entry coverage.
    if head['body']['books'] != batch['catalog']['books']: raise ValueError('certified book coverage mismatch')
    entries = [ledger.get(b['tokens_entry']) for b in batch['catalog']['books']]
    return profile, head, entries, replay


def init(root, repository, source):
    root.mkdir(parents=True, exist_ok=False)
    for name in ('cpu', 'gpu'):
        keygen(root/name/'key.private.pem', root/name/'key.public.hex')
    public = root/'public'; public.mkdir()
    evidence = public/'evidence'; evidence.mkdir()
    for name in ('genesis.json','completed-certificate.json','public-evidence.json'):
        shutil.copyfile(repository/'docs/results/profile-testnet-002'/name, evidence/name)
    _, cert = certificate(evidence)
    ws = Workspace(public)
    for path in (source/'objects').iterdir():
        if len(path.name) != 64: continue
        try: os.link(path, ws.objects.root/path.name)
        except OSError: shutil.copyfile(path, ws.objects.root/path.name)
    write_json(public/'identity.json', {'cpu':(root/'cpu/key.public.hex').read_text().strip(),
        'gpu':(root/'gpu/key.public.hex').read_text().strip(), 'genesis_sha256':GENESIS,
        'scope':'Local diagnostic keys; neither has Oracle vector-auditor authority.'})
    write_json(root/'initialized.json', {'started_utc':utc(), 'certificate':cert})
    (root/'results').mkdir()


def prepare(root, repository, name):
    start = time.perf_counter(); ws = Workspace(root/'public')
    state, cert = certificate(root/'public/evidence')
    profile, head, entries, replay = lineage(ws, state, name, ws.objects.read)
    source_profile = None; compatibility = None
    if name == 'o200k_base':
        source_profile = profile
        folder = repository/'artifacts/tokenizer-candidates/gpt-oss-20b'
        config = (folder/'config.json').read_bytes(); native = (folder/'tokenizer.json').read_bytes()
        lock = read_json(folder/'download-lock.json')
        for filename,payload in [('config.json',config),('tokenizer.json',native)]:
            if any(describe(payload)[k]!=lock['files'][filename][k] for k in ('sha256','bytes')):
                raise ValueError('GPT-OSS metadata differs from acquisition lock')
            ws.objects.put(payload)
        ranks = ws.objects.read(source_profile['body']['tokenizer']['artifact'])
        profile = bind_o200k_model(source_profile,ranks,config,'input_embedding.weight','o200k_base-gpt-oss-20b-input')
        compatibility,reference = verify_compatibility(source_profile,profile,ranks,config,native)
        compatibility['corpus'] = verify_corpus_compatibility(ws.objects,entries,reference)
        del reference
    if name == 'gpt2-baseline':
        prior = read_json(repository/'artifacts/platform-assets/embedding.json')
        upstream = {'repo':prior['upstream'], 'revision':prior['revision'], 'filename':'model.safetensors',
            'sha256':prior['weights_sha256'], 'bytes':prior['weights_bytes'], 'tensor':prior['tensor'],
            'url':f"https://huggingface.co/{prior['upstream']}/resolve/{prior['revision']}/model.safetensors"}
        path = repository/'artifacts/platform-assets/model.safetensors'
    else:
        folder = repository/'artifacts/profile-weights'/('gpt-oss-20b' if source_profile else name)
        upstream = read_json(folder/'download-lock.json'); path = folder/upstream['filename']
        if source_profile and (upstream['repo'],upstream['revision']) != (lock['repo'],lock['revision']):
            raise ValueError('GPT-OSS weights and tokenizer revisions differ')
    binding = import_embedding(ws.objects, profile, path, upstream)
    if source_profile:
        binding.update(source_profile_id=source_profile['id'],execution_profile=profile,compatibility=compatibility)
    matrix = materialize(ws.objects, profile, binding, root/'cpu'/name/'table.f32le')
    if name == 'gpt2-baseline' and binding['table']['sha256'] != prior['sha256']:
        raise ValueError('GPT-2 import does not reproduce historical input table')
    books = []; gather_seconds = 0.; dense = 0
    for entry in entries:
        data = entry['body']['payload']['data']; manifest = ws.objects.read_json(data['manifest']); records = []
        for record in manifest['records']:
            ids = unpack_ids(ws.objects.read(record['tokens']))
            if not ids or min(ids) < 0 or max(ids) >= len(matrix): raise ValueError('out of range token')
            started = time.perf_counter(); vectors = matrix[ids].tobytes()
            gather_seconds += time.perf_counter()-started; dense += len(vectors)
            records.append({k:record[k] for k in ('index','tokens','count')} | {'vectors':describe(vectors)})
        books.append({'book_id':data['book_id'], 'tokens_entry':entry['id'], 'records':records})
    body = {'schema':'hypertrix.profile-vectors/1','profile_id':profile['id'],
        'token_blueprint':head['id'], 'table_import':content_id(binding), 'contract':CONTRACT, 'books':books}
    if source_profile: body.update(schema='hypertrix.profile-vectors/2',source_profile_id=source_profile['id'])
    envelope = sign(body,load_key(root/'cpu/key.private.pem'))
    job = {'name':name, 'profile_id':profile['id'], 'blueprint':head['id'],
        'binding':ws.objects.json(binding), 'commitment':ws.objects.json(envelope),
        'cpu_public':envelope['body']['signer'], 'prepared_utc':utc(), 'cpu_gather_seconds':gather_seconds,
        'preparation_seconds':time.perf_counter()-start, 'dense_bytes':dense, 'token_replay':replay}
    if source_profile: job['source_profile_id']=source_profile['id']
    write_json(root/'public/jobs'/f'{name}.json', job)
    write_json(root/'results'/f'{name}-table-import.json', binding)
    write_json(root/'results'/f'{name}-cpu-commitment.json', envelope)
    print(json.dumps({k:job[k] for k in ('name','dense_bytes','preparation_seconds')}), flush=True)


class Fetcher:
    def __init__(self, ws, url):
        self.ws=ws; self.peer=PublicPeer(url, True); self.bytes=0; self.requests=0
    def __call__(self, desc):
        path=self.ws.objects.root/desc['sha256']
        if path.exists(): self.ws.objects.read(desc); return
        self.ws.objects.fetch(self.peer, desc); self.bytes += desc['bytes']; self.requests += 1


def reject(call):
    try: call()
    except ValueError: return True
    raise ValueError('corruption canary was accepted')


def replay(root, public, name, url):
    started=time.perf_counter(); node=root/name; node.mkdir(parents=True, exist_ok=False)
    ws=Workspace(node); fetch=Fetcher(ws,url); job=read_json(public/'jobs'/f'{name}.json')
    identities=read_json(public/'identity.json')
    if job['cpu_public'] != identities['cpu']: raise ValueError('untrusted commitment signer')
    state, cert=certificate(public/'evidence')
    profile, head, entries, checked=lineage(ws,state,name,fetch)
    fetch(job['binding']); binding=ws.objects.read_json(job['binding'])
    source_profile=None
    if name=='o200k_base':
        source_profile=profile
        if binding.get('source_profile_id')!=source_profile['id'] or job.get('source_profile_id')!=source_profile['id']:
            raise ValueError('wrong source tokenizer profile')
        profile=binding['execution_profile']; compatibility=binding['compatibility']
        for desc in descriptors(compatibility).values(): fetch(desc)
        checked_compatibility,reference=verify_compatibility(source_profile,profile,
            ws.objects.read(source_profile['body']['tokenizer']['artifact']),
            ws.objects.read(compatibility['model_config']),ws.objects.read(compatibility['upstream_tokenizer']))
        checked_compatibility['corpus']=verify_corpus_compatibility(ws.objects,entries,reference)
        del reference
        if checked_compatibility!=compatibility: raise ValueError('declared compatibility does not reproduce')
    if (job['profile_id'],job['blueprint']) != (profile['id'],head['id']): raise ValueError('job namespace mismatch')
    fetch(job['commitment']); envelope=ws.objects.read_json(job['commitment'])
    body=validate_commitment(envelope,identities['cpu'],profile,head['id'],binding,source_profile)
    tensor=binding['weights']['tensors'][profile['body']['model']['input_embedding']['name']]
    for desc in tensor['chunks']: fetch(desc)
    matrix=materialize(ws.objects,profile,binding,node/'table.f32le')
    if [(b['book_id'],b['tokens_entry']) for b in body['books']] != [(e['body']['payload']['data']['book_id'],e['id']) for e in entries]:
        raise ValueError('vector commitment book coverage mismatch')
    acquisition_seconds=time.perf_counter()-started
    gpu=CudaGather(matrix); gpu_started=time.perf_counter(); cpu_seconds=0.; dense=0; frags=0; canaries={}
    try:
        for book,entry in zip(body['books'],entries):
            manifest=ws.objects.read_json(entry['body']['payload']['data']['manifest'])
            if len(book['records']) != len(manifest['records']): raise ValueError('vector Frag coverage mismatch')
            for record,token in zip(book['records'],manifest['records']):
                ids=unpack_ids(ws.objects.read(token['tokens']))
                actual=gpu.payload(ids); before=time.perf_counter(); expected=matrix[ids].tobytes()
                cpu_seconds+=time.perf_counter()-before
                verify_frag(actual,expected,record,token,matrix.shape[1])
                if not canaries:
                    wrong=copy.deepcopy(record); wrong['vectors']['sha256']='f'*64
                    canaries['wrong_committed_hash_rejected']=reject(lambda:verify_frag(actual,expected,wrong,token,matrix.shape[1]))
                    changed=bytes([actual[0]^1])+actual[1:]
                    canaries['flipped_gpu_bit_rejected']=reject(lambda:verify_frag(changed,expected,record,token,matrix.shape[1]))
                    for label,bad in [('negative',[-1]),('too_large',[len(matrix)]),('fractional',[0.5])]:
                        canaries[label+'_token_rejected']=reject(lambda:gpu.payload(bad))
                dense+=len(actual); frags+=1
            print(json.dumps({'name':name,'book_id':book['book_id'],'checked_frags':frags}),flush=True)
        result={'schema':'hypertrix.profile-gpu-receipt/1','name':name,'profile_id':profile['id'],
            'token_blueprint':head['id'],'table_import':content_id(binding),'weights_id':binding['weights_id'],
            'cpu_commitment':envelope['id'],'accepted':True,'books':len(entries),'frags':frags,
            'tokens':checked['tokens'],'dense_bytes':dense,'table_bytes':binding['table']['bytes'],
            'http_payload_bytes':fetch.bytes,'http_requests':fetch.requests,
            'dense_to_http_payload_ratio':dense/fetch.bytes,'avoided_dense_transfer_percent':100*(1-fetch.bytes/dense),
            'download_lineage_retokenization_table_seconds':acquisition_seconds,
            'gpu_replay_including_cpu_comparison_hashing_seconds':time.perf_counter()-gpu_started,
            'total_seconds':time.perf_counter()-started,'gpu_roundtrip_seconds':gpu.roundtrip_seconds,
            'kernel_ms':gpu.kernel_ms,'gpu_launches':gpu.calls,'cpu_comparison_seconds':cpu_seconds,
            'runtime':gpu.runtime,'canaries':canaries,'source_certificate':cert,
            'implementation_sha256':implementation_digest(),'completed_utc':utc(),
            'scope':'Full local cold HTTP fetch; CUDA input row lookup, bit-exact NumPy comparison and prior CPU commitments. No contextual inference, training, WAN speed claim or Oracle-certified vector receipt.'}
        if source_profile:
            result.update(schema='hypertrix.profile-gpu-receipt/2',source_profile_id=source_profile['id'],
                          compatibility=compatibility,execution_profile_certified=False)
        if dense!=job['dense_bytes'] or frags!=checked['frags']: raise ValueError('incomplete GPU coverage')
        receipt=sign(result,load_key(root/'key.private.pem'))
        if receipt['body']['signer']!=identities['gpu']: raise ValueError('wrong GPU diagnostic identity')
        write_json(root/'results'/f'{name}-gpu-receipt.json',receipt)
        print(json.dumps({k:result[k] for k in ('name','accepted','frags','dense_bytes','http_payload_bytes','total_seconds')}),flush=True)
    finally: gpu.close()


def index(root):
    objects=Objects(root/'public/objects')
    from dawl_swarm.dht import atomic
    atomic(root/'public/artifact-index.json', {p.name:{'sha256':p.name,'bytes':p.stat().st_size} for p in objects.root.iterdir() if len(p.name)==64})


def summarize(root, names=None):
    if names is None: names=sorted(p.stem for p in (root/'public/jobs').glob('*.json'))
    identities=read_json(root/'public/identity.json'); reports=[]
    for name in names:
        receipt=read_json(root/'gpu/results'/f'{name}-gpu-receipt.json')
        result=verify_signature(receipt,identities['gpu']); reports.append(result)
        shutil.copyfile(root/'gpu/results'/f'{name}-gpu-receipt.json',root/'results'/f'{name}-gpu-receipt.json')
    report={'schema':'hypertrix.multi-profile-gpu-pilot/1','completed_utc':utc(), 'profiles':reports,
        'all_accepted':all(r['accepted'] for r in reports), 'books_per_profile':13,
        'profile_frags':sum(r['frags'] for r in reports),'dense_bytes':sum(r['dense_bytes'] for r in reports),
        'http_payload_bytes':sum(r['http_payload_bytes'] for r in reports),
        'skipped':[],
        'scope':'Separate cold-cache GPU processes, one provider container and one GPU at a time on a single local host. CPU commitments precede GPU replay; same operator controls both diagnostic keys. Token Blueprint quorum is historical; vector diagnostics and derived execution profiles are not committed to Oracle.'}
    write_json(root/'results/report.json',report); write_json(root/'results/identity.json',identities)
    print(json.dumps({k:report[k] for k in ('all_accepted','profile_frags','dense_bytes','http_payload_bytes')}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('command',choices=['init','prepare','index','replay','summarize'])
    p.add_argument('--root',type=Path,default=Path('/run')); p.add_argument('--public',type=Path,default=Path('/public'))
    p.add_argument('--repository',type=Path,default=Path('/repo')); p.add_argument('--source',type=Path,default=Path('/source'))
    p.add_argument('--name',choices=NAMES); p.add_argument('--provider',default='http://127.70.0.28:8900')
    p.add_argument('--names',nargs='+',choices=NAMES)
    a=p.parse_args()
    if a.command=='init': init(a.root,a.repository,a.source)
    elif a.command=='prepare': prepare(a.root,a.repository,a.name)
    elif a.command=='index': index(a.root)
    elif a.command=='replay': replay(a.root,a.public,a.name,a.provider)
    else: summarize(a.root,a.names)
