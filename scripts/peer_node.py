"""Discovery transport used by every Hypertrix node, regardless of its execution hat."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
import uvicorn
from dawl_swarm.dht import DHT

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--name',required=True);p.add_argument('--host',required=True);p.add_argument('--roles',required=True)
    p.add_argument('--seeds',required=True);a=p.parse_args()
    cfg=json.loads((a.root/'cluster.json').read_text())
    dht=DHT(a.root,cfg['network_sha256'],a.name,a.host,a.roles.split(','),json.loads(a.seeds))
    uvicorn.run(dht.app(),host=a.host,port=8903,access_log=False,limit_concurrency=64)
