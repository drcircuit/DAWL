"""Verify the handoff ZIP without extracting it. Requires Python + cryptography.

Checks package bytes, GPU Ed25519 signature, hash bindings and historical DHT
records. Does NOT verify CometBFT quorum signatures or current network health.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PUBLIC='581e0bb4da820894c02ed51684268c7b17a84fdcd3c408f19eb58559a8cc20f5'
GENESIS='8443e106c542c1e920e51862b9763300bc2079871f9d03f1cc3c46ded5de1871'
RELEASE='fd8760e4f3878a1bcb79645f42f0f55bcbbaab74f20d9aa2cfe53ac005743950'
DOMAIN=b'DAWL/ingestion-node/v2\x00'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def sha(data): return hashlib.sha256(data).hexdigest()


def signature(envelope, public):
    assert set(envelope)=={'body','id','signature'}
    body=envelope['body'];payload=canonical(body)
    assert body['signer']==public and sha(payload)==envelope['id']
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(bytes.fromhex(envelope['signature']),DOMAIN+payload)
    return body


def verify(path):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        assert len(names)==len(set(names))
        for name in names:
            p=PurePosixPath(name)
            assert not p.is_absolute() and '..' not in p.parts and '\\' not in name
        assert sum(i.file_size for i in z.infolist())<32*1024**2
        manifest=json.loads(z.read('MANIFEST.json'))
        assert set(names)==set(manifest['files'])|{'MANIFEST.json'}
        for name,expected in manifest['files'].items():
            data=z.read(name)
            assert len(data)==expected['bytes'] and sha(data)==expected['sha256'],name
        load=lambda name:json.loads(z.read(name))
        report=load('evidence/gpu/last-report.json');body=signature(report,PUBLIC)
        assert z.read('evidence/gpu/public.key').decode().strip()==PUBLIC
        assert sha(z.read('evidence/gpu/genesis.json'))==GENESIS==body['genesis_sha256']
        assert body['accepted'] and not body['training_executed'] and not body['consensus_receipt']
        assert body['books']==13 and body['chunks']==body['cuda_launches']==1807
        assert body['tokens']==2061330 and body['dense_bytes']==body['tokens']*768*4
        assert body['persisted_dense_vector_bytes']==0
        assert all(not r['cpu_fallback'] for r in body['runtimes'])
        state=load('evidence/gpu/input-state.json')
        assert body['input_state_sha256']==sha(canonical(state))
        assert body['certified_height']==state['height']
        assert body['dataset_release']==state['dataset_release']['id']==RELEASE
        assert sha(canonical(state['dataset_release']['body']))==RELEASE
        cert=load('evidence/gpu/public-certificate.json')
        assert cert['genesis_sha256']==GENESIS
        assert cert['app_hash']==sha(canonical(cert['state']))
        assert cert['state']['dataset_release']['id']==RELEASE
        assert PUBLIC not in cert['state']['actors'] and PUBLIC not in cert['state']['plan']['actors']
        discovery=load('evidence/gpu/discovery-verified.json')
        assert len(discovery['peers'])==discovery['count']==29
        assert len({r['host'] for r in discovery['peers']})==29
        for item in discovery['peers']:
            record=signature(item['record'],PUBLIC);now=item['checked_at_unix']
            assert record['issued']<=now+30 and now<record['expires']<=record['issued']+180
            assert record['network']==body['network_sha256'] and record['roles']==['AUDITOR']
        for name,digest in body['scripts'].items():
            assert sha(z.read('implementation/scripts/'+name))==digest
        for name,expected in [('pooled',305.112973),('per-node',252.343367)]:
            result=load('evidence/'+name+'/results/report.json')
            assert result['completed'] and result['seconds']==expected
            assert result['attestations']==52 and result['verified_chunk_executions']==2876
        inputs=load('evidence/contribution/keyless-input-check.json')
        assert inputs['source_tokens']-inputs['chunks']==inputs['target_tokens']==2059523
        assert len(body['results'])==13 and sum(r['checked_chunks'] for r in body['results'])==1807
        return dict(verified_files=len(manifest['files']),gpu_report=report['id'],gpu_chunks=1807,
                    historical_discovery_peers=29,full_quorum_signatures_verified_here=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('zip',type=Path);a=p.parse_args()
    print(json.dumps(verify(a.zip),indent=2))
