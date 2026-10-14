"""Add one private GPU DHT route to the existing gateway, without restarting nodes."""
import base64
import json
from control import REPO, TOOLS, save
from launch import command


def main():
    root = REPO/'artifacts/azure-hybrid-001'
    run = json.loads((root/'deployment.json').read_text())
    assert run['subscription'] == '58eadcc1-71bf-4547-bd13-7f68a6042a0f'
    assert run['resource_group'] == 'DAWL'
    code = """from pathlib import Path
import os,signal,subprocess
p=Path('/run/dawl-ssh/config');old=p.read_text();rows=old.splitlines()
assert len([r for r in rows if r.startswith('PermitListen ')])==1
new='\\n'.join(r+' 127.70.0.23:8903' if r.startswith('PermitListen ') and '127.70.0.23:8903' not in r.split() else r for r in rows)+'\\n'
if new!=old:
 p.with_suffix('.before-gpu').write_text(old);p.write_text(new)
 try:subprocess.run(['/usr/sbin/sshd','-t','-f',str(p)],check=True)
 except BaseException:p.write_text(old);raise
 os.kill(int(Path('/run/dawl-ssh/pid').read_text()),signal.SIGHUP)
print('GPU_PRIVATE_DHT_ALLOWED')
"""
    expression = "python -c exec(__import__('base64').b64decode('"+base64.b64encode(code.encode()).decode()+"'))"
    output = command(TOOLS, '-m', 'azure.cli', 'container', 'exec', '--subscription', run['subscription'],
        '-g', 'DAWL', '-n', run['prefix']+'-nodes', '--container-name', 'gateway',
        '--exec-command', expression, '--only-show-errors', timeout=60).decode(errors='replace')
    if 'GPU_PRIVATE_DHT_ALLOWED' not in output: raise RuntimeError('gateway change not confirmed: '+output[-1000:])
    save(root/'gpu-gateway-route.json', dict(private_listen='127.70.0.23:8903', confirmed=True,
        public_ports_added=[], node_containers_restarted=False))
    print('Confirmed exact private GPU discovery route; no public port added')


if __name__ == '__main__': main()
