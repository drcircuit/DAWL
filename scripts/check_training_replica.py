"""Cold public consumer with only the surviving training replica reachable."""
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
from dawl_node.common import read_json,write_json,verify_signature
from dawl_platform.artifacts import Objects,PublicPeer
from dawl_training.network import Network
from dawl_training.protocol import validate_stage,certify

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/consumer'));p.add_argument('--stage',type=Path,default=Path('/stage.json'))
a=p.parse_args(); stage=read_json(a.stage); b=validate_stage(stage)
objects=Objects(a.root/'objects'); peer=PublicPeer(b['participants'][1]['url'],True)
release=peer.json('/release'); audit=peer.json('/audit')
checked=verify_signature(audit,b['auditor'])
if (release!=certify(release['body'],release['acceptances'],stage)
        or release['body']['stage']!=stage['id'] or release['body']['audit']!=audit['id']
        or release['body']['checkpoint']!=checked['checkpoint'] or checked['stage']!=stage['id']
        or release['body']['parent_model']!=b['parent_model']['sha256']
        or release['body']['fixed_projection']!=b['fixed_projection']
        or release['body']['profile_id']!=b['profile']['id']): raise ValueError('invalid public model release')
network=Network(objects,[peer.url]); digest=hashlib.sha256(); count=0
for desc in b['parent_model']['chunks']:
    network.fetch(desc); data=objects.read(desc); digest.update(data); count+=len(data)
if digest.hexdigest()!=b['parent_model']['sha256'] or count!=b['parent_model']['bytes']: raise ValueError('parent model incomplete')
for desc in (b['fixed_projection'],release['body']['adapter'],b['profile']['body']['model']['config'],b['profile']['body']['tokenizer']['artifact']):
    network.fetch(desc)
write_json(a.root/'result.json',{'verified':True,'stage':stage['id'],'release':release['id'],
    'sole_source':peer.url,'artifact_bytes':network.bytes,'objects':network.requests,
    'full_parent_checkpoint_verified':True,'fixed_projection_and_trained_adapter_verified':True,
    'scope':'Cold download from peer 1 after original Cortex and peer 0 were stopped. Public integrity/signature verification; no private key or training execution.'})
print((a.root/'result.json').read_text(),flush=True)
