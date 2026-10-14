"""Seal the final reviewed evidence bundle, including figures and its explanation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_node.common import load_key,sign,write_json
from verify_seed_release import digest,require,verify,verify_release


def seal(root,private_key):
    root=Path(root);private_key=Path(private_key)
    require(not private_key.resolve().is_relative_to(root.resolve()),'Signing key must stay outside the public bundle')
    report=json.loads((root/'report.json').read_bytes());verify(root,report,metadata_only=True)
    paths=[p for p in root.rglob('*') if p.is_file() and p.name not in ('sha256-manifest.json','release-attestation.json')]
    require(all('private' not in p.name.lower() and p.suffix.lower() in ('.json','.jsonl','.py','.key','.md','.txt','.png','.pdf') for p in paths),'Unexpected export file')
    manifest={p.relative_to(root).as_posix():digest(p) for p in sorted(paths)}
    write_json(root/'sha256-manifest.json',manifest)
    envelope=sign({'schema':'hypertrix.seed-release/2','report_sha256':digest(root/'report.json'),
        'artifact_verification_sha256':digest(root/'artifact-verification.json'),
        'manifest_sha256':digest(root/'sha256-manifest.json')},load_key(private_key))
    write_json(root/'release-attestation.json',envelope)
    return verify_release(root,report['operator_public_key'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--private-key',type=Path,required=True);a=p.parse_args()
    print(json.dumps(seal(a.root,a.private_key)))
