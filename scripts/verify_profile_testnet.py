"""Offline signature/namespace/coverage checks of exported public pilot evidence.

The CometBFT quorum certificate is verified during capture by dawl-consensus;
consensus/cmd/check-certificate supplies an independent offline vote check.
This script checks its state-byte hash and binds individual Ed25519 receipts and
signed lineage bundles to it; it does not reimplement CometBFT vote verification.
"""
import argparse
from pathlib import Path
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import canonical_json, read_json, sha256, verify_signature
from dawl_profiles.bundles import import_bundle
from dawl_profiles.profiles import content_id, describe, validate_profile


def verify(root):
    cert=read_json(root/'completed-certificate.json'); state=cert['state']; evidence=read_json(root/'public-evidence.json')
    header=cert['signed_header']['header']
    if (sha256(canonical_json(state))!=cert['app_hash'] or header['app_hash'].lower()!=cert['app_hash']
            or int(header['height'])!=state['height']+1 or header['chain_id']!=state['chain_id']
            or content_id(evidence['genesis'])!=cert['genesis_sha256']):
        raise ValueError('certificate state or genesis hash mismatch')
    transactions={tx['id']:tx for tx in evidence['audit_transactions']}
    wanted=set(); count=0
    for pid,profile in state['profiles'].items():
        validate_profile(profile); head=state['profile_heads'][pid]
        if not head or content_id(head['body'])!=head['id'] or head['body']['profile_id']!=pid: raise ValueError('wrong profile head')
        profile_receipts=[]
        for jid,job in state['profile_jobs'].items():
            spec=job['spec']
            if spec['profile_id']!=pid: continue
            if content_id(spec)!=jid or len(job['attestations'])<state['plan']['replicas']: raise ValueError('incomplete job')
            for public,audit in job['attestations'].items():
                tx=transactions[audit['transaction']]; body=verify_signature(tx,public); payload=body['payload']
                if ('AUDIT_CPU' not in state['plan']['actors'][public]['hats'] or body['kind']!='result_profile'
                        or body['chain_id']!=state['chain_id'] or body['plan_sha256']!=state['plan_sha256']
                        or payload['profile_id']!=pid or payload['job']!=jid or payload['report']!=audit['report']
                        or payload['report']['outcome']!='pass' or payload['report']['checked_chunks']!=spec['frags']
                        or payload['report']['tokens_checked']!=spec['tokens']): raise ValueError('receipt binding mismatch')
                wanted.add(tx['id']); profile_receipts.append(tx['id']); count+=1
        if sorted(profile_receipts)!=head['body']['receipts']: raise ValueError('Blueprint receipt coverage differs')
    if wanted!=transactions.keys(): raise ValueError('missing or extra receipts')
    with tempfile.TemporaryDirectory() as directory:
        bundles={describe(canonical_json(b))['sha256']:b for b in evidence['bundles']}
        for identity,batch in state['profile_contributions'].items():
            envelope=bundles[batch['bundle']['sha256']]
            if describe(canonical_json(envelope))!=batch['bundle']: raise ValueError('bundle hash or length mismatch')
            import_bundle(Path(directory)/identity,envelope,batch,state['profiles'][batch['profile_id']])
    return {'profiles':len(state['profiles']),'jobs':len(state['profile_jobs']),'signed_receipts_verified':count,
            'lineage_bundles_verified':len(evidence['bundles']),'state_hash_verified':True,
            'quorum_scope':'Verify vote signatures separately with consensus/cmd/check-certificate and the independently pinned genesis.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT/'docs/results/profile-testnet-002')
    print(canonical_json(verify(parser.parse_args().root)).decode())
