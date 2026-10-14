"""Export public quorum evidence from the isolated local protocol-3 pilot."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import canonical_json, write_json, sha256


def execute(*args):
    return subprocess.run(['wsl','-d','kali-linux','-u','root','--',*args], check=True,
        capture_output=True, timeout=60).stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('label',choices=['status','before-outage','during-outage','interrupted','completed'])
    parser.add_argument('--run',default='002',choices=['001','002'])
    args=parser.parse_args(); root=ROOT/('docs/results/profile-testnet-'+args.run); root.mkdir(parents=True,exist_ok=True)
    native='/var/lib/dawl/profile-v3-'+args.run+'/state'
    validator='hypertrix-p3-'+('' if args.run=='001' else args.run+'-')+'validator-0'
    config=json.loads(execute('cat',native+'/publisher/cluster.json'))
    certificate=json.loads(execute('podman','exec',validator,'dawl-consensus','verify',
        '--rpc','http://127.71.0.10:26657','--genesis','/node/genesis.json','--genesis-sha256',config['genesis_sha256'],
        '--min-height','1','--max-age','120s'))
    if certificate['verified'] is not True: raise ValueError('unverified result')
    state=certificate['state']
    if args.label!='status': write_json(root/(args.label+'-certificate.json'),certificate)
    status={'height':state['height'],'app_hash':sha256(canonical_json(state)),
            'complete':sum(len(j['attestations'])>=2 for j in state['profile_jobs'].values()),
            'receipts':sum(len(j['attestations']) for j in state['profile_jobs'].values()),
            'heads':sum(bool(h) for h in state['profile_heads'].values())}
    if args.label!='status': write_json(root/(args.label+'.json'),status)
    if args.label=='completed':
        write_json(root/'report.json',json.loads(execute('cat',native+'/report.json')))
        evidence=json.loads(execute('cat',native+'/public-evidence.json'))
        write_json(root/'public-evidence.json',evidence); write_json(root/'genesis.json',evidence['genesis'])
        image=execute('podman','image','inspect','localhost/hypertrix-profiles:v3','--format','{{.Id}}').decode().strip()
        write_json(root/'implementation.json',{'container_image_id':image,
            'files':{p.relative_to(ROOT).as_posix():sha256(p.read_bytes()) for p in sorted((ROOT/'src').rglob('*.py'))},
            'reproduction_runner_sha256':sha256((ROOT/'scripts/profile_testnet.py').read_bytes())})
    print(json.dumps(status,indent=2))


if __name__=='__main__':main()
