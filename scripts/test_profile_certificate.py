"""Exercise the offline Go quorum verifier against genuine and altered evidence."""
import argparse
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import canonical_json, read_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--certificate',default='before-outage-certificate.json')
    args=parser.parse_args(); root=ROOT/'docs/results/profile-testnet-002'
    cert=read_json(root/args.certificate); tmp=ROOT/'artifacts/certificate-negative-tests'; tmp.mkdir(exist_ok=True)
    changed=copy.deepcopy(cert); changed['state']['height']+=1
    (tmp/'state.json').write_bytes(canonical_json(changed))
    changed=copy.deepcopy(cert)
    for signature in changed['signed_header']['commit']['signatures']:
        if signature['block_id_flag']==2: signature['signature']=base64.b64encode(b'\0'*64).decode()
    (tmp/'votes.json').write_bytes(canonical_json(changed))
    changed=copy.deepcopy(cert); kept=0
    for signature in changed['signed_header']['commit']['signatures']:
        if signature['block_id_flag']==2 and kept<2: kept+=1
        else:
            signature.update(block_id_flag=1,validator_address='',timestamp='0001-01-01T00:00:00Z',signature=None)
    (tmp/'insufficient.json').write_bytes(canonical_json(changed))
    results={}
    for name,path,pin,expected in [
        ('genuine',root/args.certificate,cert['genesis_sha256'],0),
        ('changed_state',tmp/'state.json',cert['genesis_sha256'],1),
        ('forged_votes',tmp/'votes.json',cert['genesis_sha256'],1),
        ('insufficient_quorum',tmp/'insufficient.json',cert['genesis_sha256'],1),
        ('wrong_genesis_pin',root/args.certificate,'0'*64,1)]:
        result=subprocess.run(['wsl','-d','kali-linux','-u','root','--','./artifacts/check-certificate',
            '--genesis',(root/'genesis.json').relative_to(ROOT).as_posix(),
            '--genesis-sha256',pin,'--certificate',path.relative_to(ROOT).as_posix()],
            cwd=ROOT,capture_output=True,text=True,timeout=30)
        if result.returncode!=expected: raise RuntimeError(name+': '+result.stderr)
        results[name]={'expected_exit_code':expected,'actual_exit_code':result.returncode,
                       'accepted':result.returncode==0,'diagnostic':result.stderr.strip()[:300]}
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
