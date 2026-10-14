"""Deliver public bootstrap through authenticated ACI exec when ingress is not ready."""
import base64
import hashlib
import json
from pathlib import Path
from control import REPO,TOOLS,az,save
from launch import command
from hybrid import WSL


def main():
    root=REPO/'artifacts/azure-hybrid-001';run=json.loads((root/'deployment.json').read_text())
    native='/var/lib/dawl/'+run['prefix']+'-nodes'
    command(*WSL,'python3','scripts/upgrade_operator.py','stage-public')
    payload=command(*WSL,'cat',native+'/upgrade-public.tar.gz')
    if len(payload)>200000:raise ValueError('public bootstrap exceeds management-transfer bound')
    digest=hashlib.sha256(payload).hexdigest();directory='/exchange/bootstrap-chunks/'+digest
    scope=['--subscription',run['subscription'],'-g',run['resource_group'],'-n',run['prefix']+'-nodes']
    def execute(code):
        # ACI splits command text on spaces; one base64 Python expression avoids shell quoting entirely.
        expression="python -c exec(__import__('base64').b64decode('"+base64.b64encode(code.encode()).decode()+"'))"
        output=command(TOOLS,'-m','azure.cli','container','exec',*scope,'--container-name','gateway','--exec-command',expression,'--only-show-errors').decode()
        if 'PUBLIC_BOOTSTRAP_OK' not in output:raise RuntimeError(output[-1500:])
    chunks=[payload[i:i+1800] for i in range(0,len(payload),1800)]
    for i,chunk in enumerate(chunks):
        code="from pathlib import Path;import base64;p=Path("+repr(directory+'/'+str(i))+");p.parent.mkdir(parents=True,exist_ok=True);data=base64.b64decode("+repr(base64.b64encode(chunk).decode())+");assert not p.exists() or p.read_bytes()==data;p.write_bytes(data);print('PUBLIC_BOOTSTRAP_OK')"
        execute(code);print(f'Public bootstrap part {i+1}/{len(chunks)}',flush=True)
    code=f"""from pathlib import Path
import hashlib,io,tarfile,os
base=Path({directory!r});data=b''.join((base/str(i)).read_bytes() for i in range({len(chunks)}))
assert hashlib.sha256(data).hexdigest()=={digest!r}
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tar:
    for member in tar:
        target=(Path('/exchange')/member.name).resolve()
        assert target.is_relative_to(Path('/exchange')) and member.name.split('/')[0] in ('bundle','runtime-upgrade.json','observer','network.sha256')
        assert member.isdir() or member.isfile()
    tar.extractall('/exchange',filter='data')
print('PUBLIC_BOOTSTRAP_OK')
"""
    execute(code)
    save(root/'management-bootstrap.json',dict(sha256=digest,bytes=len(payload),parts=len(chunks),private_keys_transferred=False))
    print('Public bootstrap delivered through authenticated Azure management.',flush=True)


if __name__=='__main__':main()
