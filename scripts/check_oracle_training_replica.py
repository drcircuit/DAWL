"""Cold consumer: pinned certificate, one remaining verifier, full model bundle."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts'))
from dawl_node.common import read_json,write_json
from dawl_profiles.workspace import Workspace
from dawl_training.network import Network
from dawl_training.model import assemble,load_model,restore,export,evaluate
from dawl_training.oracle import lineage
from dawl_training.work import validate_manifest
from verify_oracle_training import verify


def check(evidence, root, url, pin):
    verification=verify(evidence,pin)
    if root.exists():raise ValueError('cold consumer needs empty state directory')
    ws=Workspace(root); network=Network(ws.objects,[url],root/'transfers.jsonl')
    state=read_json(evidence/'training-certificate.json')['state']
    stage=state['training_stages'][verification['stage']]
    network.fetch(stage['body']['manifest']); manifest=ws.objects.read_json(stage['body']['manifest'])
    dataset=lineage(ws,state,stage,network); network.fetch(manifest['dataset'])
    if dataset!=ws.objects.read_json(manifest['dataset']):raise ValueError('source windows differ')
    validate_manifest(manifest,stage,dataset)
    for desc in manifest['parent_model']['chunks']+[manifest['fixed_projection'],stage['adapter']]:network.fetch(desc)
    path=assemble(ws.objects,manifest['parent_model'],root/'model.safetensors')
    model=load_model(ws.objects.read(manifest['profile']['body']['model']['config']),path)
    restore(model,ws.objects.read(manifest['fixed_projection']),manifest['projection_layout'],'A')
    restore(model,ws.objects.read(stage['adapter']),manifest['adapter_layout'])
    # Small CPU forward pass confirms that the downloadable composite is executable.
    loss=evaluate(model,dataset['validation'][:2],ws.objects,'cpu')
    result={'source_peer':url,'checkpoint':stage['head'],'parent_model':manifest['parent_model']['sha256'],
            'adapter':stage['adapter'],'downloaded_bytes':network.bytes,'objects':network.requests,
            'quorum_verified':True,'state_replayed':True,'source_retokenized':True,
            'model_loaded':True,'cpu_validation':loss,
            'scope':'One surviving verifier supplied a cold consumer after original provider and all trainers stopped. Local host only.'}
    write_json(root/'availability.json',result);print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--peer',required=True);p.add_argument('--genesis-sha256',required=True)
    a=p.parse_args();check(a.evidence,a.root,a.peer,a.genesis_sha256)
