"""Retain the certified recovered head on the Cortex and check an uncached pull."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request
from dawl_node.common import read_json, write_json
from dawl_profiles.workspace import Workspace
from dawl_swarm.dht import atomic


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','check']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--genesis-sha256',required=True);a=p.parse_args();root=a.root; evidence=root/'evidence'
    subprocess.run(['check-certificate','-genesis',str(evidence/'genesis.json'),'-genesis-sha256',a.genesis_sha256,
                    '-certificate',str(evidence/'training-certificate.json')],check=True,capture_output=True)
    state=read_json(evidence/'training-certificate.json')['state']; sid=read_json(evidence/'stage-c.json')['id']
    stage=state['training_stages'][sid]
    if stage['status']!='complete':raise ValueError('complete certified stage required')
    adapter=stage['adapter']; endpoint=next(iter(state['providers'].values()))['endpoint']
    provider=Workspace(root/'state/provider').objects
    if a.command=='prepare':
        source=Workspace(root/'state/runs/c/verifier-0/public').objects
        if provider.put(source.read(adapter))!=adapter:raise ValueError('replica differs from certified adapter')
        atomic(root/'state/provider/artifact-index.json',{f.name:{'sha256':f.name,'bytes':f.stat().st_size}
            for f in provider.root.iterdir() if len(f.name)==64})
        print('Certified final adapter retained on Cortex; reload provider index.',flush=True)
    else:
        start=time.perf_counter()
        # A new client with no cache; provider is read through its real HTTP endpoint.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(endpoint+'/api/objects/'+adapter['sha256'],timeout=15) as response:
            data=response.read(adapter['bytes']+1)
        if len(data)!=adapter['bytes'] or hashlib.sha256(data).hexdigest()!=adapter['sha256']:
            raise ValueError('uncached HTTP artifact differs from certified head')
        value={'schema':'hypertrix.recovery-availability/1','stage':sid,'checkpoint':stage['head'],
               'adapter':adapter,'provider':next(iter(state['providers'])),'uncached_http_hash_match':True,
               'bytes_received':len(data),'seconds':time.perf_counter()-start,
               'scope':'Point-in-time artifact retrieval after all trial GPU workers stopped; not a continuing possession proof.'}
        write_json(evidence/'availability.json',value);print(json.dumps(value),flush=True)


if __name__=='__main__':main()
