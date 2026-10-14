"""Check the signed diagnostic manifest against all retained binary artifacts."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
from dawl_node.common import read_json,write_json,verify_signature
from dawl_finetune.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--trusted-public',required=True)
a=p.parse_args();root=a.root.resolve()
result=read_json(root/'replay-verification.json');body=verify_signature(read_json(root/'attestation.json'),a.trusted_public)
if body['result_sha256']!=file_hash(root/'replay-verification.json'):raise ValueError('Result digest mismatch')
if body['source_lineage_head']!=result['source_lineage_head']:raise ValueError('Parent lineage mismatch')
for name,meta in result['files'].items():
    path=(root/name).resolve()
    if not path.is_relative_to(root):raise ValueError('Unsafe artifact path')
    if path.stat().st_size!=meta['bytes'] or file_hash(path)!=meta['sha256']:raise ValueError('Artifact mismatch: '+name)
receipt={'schema':'hypertrix.repeatability-artifact-check/1','passed':True,'checked_files':len(result['files']),
         'trusted_operator_public_key':a.trusted_public,'result_sha256':body['result_sha256'],
         'verified_utc':datetime.now(timezone.utc).isoformat(),'verifier_script_sha256':file_hash(Path(__file__)),
         'scope':'Signatures, signed metadata and every retained binary file. Computational repeatability is a separate experimental result.'}
write_json(root/'artifact-verification.json',receipt);print(receipt)
