"""Negative checks for the exported recovery evidence; modifies temporary copies only."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    pin=json.loads((a.root/'trial.json').read_text())['genesis_sha256'];results=[]
    for case in ('missing_transaction','changed_manifest','missing_replay'):
        with tempfile.TemporaryDirectory(prefix='hypertrix-evidence-check-') as tmp:
            root=Path(tmp)/'evidence';shutil.copytree(a.root,root)
            if case=='missing_transaction':
                path=root/'application-history.json';value=json.loads(path.read_text())
                first=min(value['blocks'],key=int);value['blocks'][first].pop(0);path.write_text(json.dumps(value))
            elif case=='changed_manifest':
                path=root/'manifest-c.json';value=json.loads(path.read_text());value['recipe']['learning_rate']=0.2
                path.write_text(json.dumps(value))
            else:next((root/'workers/c/verifier-1/public').glob('replay-*.json')).unlink()
            checked=subprocess.run([sys.executable,'-P',str(a.root/'scripts/verify_recovery_pilot.py'),
                '--root',str(root),'--genesis-sha256',pin],capture_output=True,text=True)
            if checked.returncode==0:raise ValueError('corrupt archive accepted: '+case)
            reason=checked.stderr.strip().splitlines()[-1]
            if not reason.startswith('ValueError:'):raise ValueError('unexpected checker failure: '+reason)
            results.append({'case':case,'rejected':True,'reason':reason})
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
