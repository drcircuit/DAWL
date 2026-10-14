"""Offline signature and lineage-binding verification of the exported GPU report.

This verifies attestations and exact coverage, not the computation itself. Replay
the public corpus/weight artifacts with profile_gpu_pilot.py for numerical checks.
"""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import read_json, verify_signature
from dawl_profiles.profiles import content_id, validate_weights, descriptor
from dawl_profiles.vectors import validate_commitment


DEFAULT_NAMES=('gpt2-baseline','smollm2','qwen3','qwen35','smollm3')


def verify(root, source, expected_names=DEFAULT_NAMES):
    report=read_json(root/'report.json'); identity=read_json(root/'identity.json')
    certificate=read_json(source/'completed-certificate.json'); state=certificate['state']
    from dawl_node.common import canonical_json, sha256
    if sha256(canonical_json(state))!=certificate['signed_header']['header']['app_hash'].lower():
        raise ValueError('source certificate state hash mismatch')
    if identity['genesis_sha256']!=certificate['genesis_sha256']: raise ValueError('wrong genesis pin')
    names=[r['name'] for r in report['profiles']]
    if sorted(names)!=sorted(expected_names) or len(set(names))!=len(names):
        raise ValueError('missing or duplicate model profile')
    total_dense=total_payload=total_frags=0
    for item in report['profiles']:
        name=item['name']; receipt=read_json(root/f'{name}-gpu-receipt.json')
        body=verify_signature(receipt,identity['gpu'])
        if body!=item: raise ValueError('summary differs from signed receipt')
        binding=read_json(root/f'{name}-table-import.json')
        source_profile=None
        if name=='o200k_base':
            source_profile=state['profiles'][body['source_profile_id']]
            profile=binding['execution_profile']; head=state['profile_heads'][source_profile['id']]
            compatibility=binding['compatibility']
            if (body['schema']!='hypertrix.profile-gpu-receipt/2' or body['execution_profile_certified'] is not False
                    or body['profile_id']!=profile['id'] or body['compatibility']!=compatibility
                    or source_profile['body']['model'] is not None
                    or profile['body']['tokenizer']!=source_profile['body']['tokenizer']
                    or profile['body']['chunking']!=source_profile['body']['chunking']
                    or profile['body']['model']['family']!='gpt_oss'
                    or compatibility['source_profile_id']!=source_profile['id']
                    or compatibility['execution_profile_id']!=profile['id']
                    or compatibility['rank_artifact']!=source_profile['body']['tokenizer']['artifact']
                    or compatibility['model_config']!=profile['body']['model']['config']
                    or compatibility['ordinary_rows_verified']!=199998 or compatibility['max_ordinary_token_id']!=199997
                    or compatibility['corpus']!={'frags':body['frags'],'tokens':body['tokens'],'exact_ordinary_encoding_match':True}):
                raise ValueError('o200k compatibility binding mismatch')
        else:
            profile=state['profiles'][body['profile_id']]; head=state['profile_heads'][profile['id']]
        if validate_weights(binding['weights'],profile)!=body['weights_id']: raise ValueError('wrong weight layout')
        commitment=read_json(root/f'{name}-cpu-commitment.json')
        cpu=validate_commitment(commitment,identity['cpu'],profile,head['id'],binding,source_profile)
        if (body['table_import']!=content_id(binding) or body['cpu_commitment']!=commitment['id']
                or body['token_blueprint']!=head['id'] or body['source_certificate']['app_hash']!=certificate['app_hash']
                or body['name']!=(source_profile or profile)['body']['label'] or body['table_bytes']!=binding['table']['bytes']):
            raise ValueError('GPU receipt binding mismatch')
        books=head['body']['books']; width=profile['body']['model']['input_embedding']['shape'][1]
        if [(b['book_id'],b['tokens_entry']) for b in books]!=[(b['book_id'],b['tokens_entry']) for b in cpu['books']]:
            raise ValueError('book coverage mismatch')
        frags=tokens=dense=0
        for expected,book in zip(books,cpu['books']):
            if len(book['records'])!=expected['frags']: raise ValueError('Frag coverage mismatch')
            n=0
            for i,record in enumerate(book['records']):
                descriptor(record['tokens']); descriptor(record['vectors'])
                if (record['index']!=i or type(record['count']) is not int or record['count']<=0
                        or record['tokens']['bytes']!=record['count']*4
                        or record['vectors']['bytes']!=record['count']*width*4): raise ValueError('invalid record layout')
                n+=record['count']; dense+=record['vectors']['bytes']; frags+=1
            if n!=expected['tokens']: raise ValueError('token coverage mismatch')
            tokens+=n
        if ((len(books),frags,tokens,dense)!=(body['books'],body['frags'],body['tokens'],body['dense_bytes'])
                or body['gpu_launches']!=frags or not body['accepted'] or body['runtime']['cpu_fallback']
                or not all(body['canaries'].values())): raise ValueError('GPU totals or status mismatch')
        total_dense+=dense; total_payload+=body['http_payload_bytes']; total_frags+=frags
    if (total_dense,total_payload,total_frags)!=(report['dense_bytes'],report['http_payload_bytes'],report['profile_frags']):
        raise ValueError('combined report totals mismatch')
    return {'verified':True,'signed_cpu_commitments':len(names),'signed_gpu_receipts':len(names),'profile_frags':total_frags,
        'dense_bytes':total_dense,'scope':'Offline signatures and binding/coverage checks. Numerical replay and source CometBFT quorum checks are separate.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT/'docs/results/profile-gpu-run-001')
    p.add_argument('--source',type=Path,default=ROOT/'docs/results/profile-testnet-002')
    p.add_argument('--names',nargs='+',default=list(DEFAULT_NAMES))
    from dawl_node.common import canonical_json
    a=p.parse_args(); print(canonical_json(verify(a.root,a.source,a.names)).decode())
