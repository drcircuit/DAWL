"""Start an isolated WSL/Podman GPU diagnostic node from public bootstrap only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from hybrid_operator import run, save
from node_operator import NodeOperator


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('/var/lib/dawl/dawlh1013185918-nodes'))
    p.add_argument('--forward', action='store_true', help='Requires exact gateway PermitListen rule first')
    a = p.parse_args(); root = a.root; operator = NodeOperator(root)
    deadline = operator.config['deadline']
    if not 0 < (datetime.fromisoformat(deadline)-datetime.now(timezone.utc)).total_seconds() <= 86400:
        raise ValueError('pilot deadline expired or unbounded')
    if a.forward:
        # Existing sshd sessions retain their original PermitListen policy.
        # A separate bounded-pilot connection picks up the newly validated rule.
        run(*operator.ssh_base, '-M', '-S', str(root/'gpu.sock'), '-f', '-N',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', '-R',
            '127.70.0.23:8903:127.70.0.23:8903', operator.target)
        print('GPU DHT route forwarded through existing authenticated tunnel', flush=True)
    name = operator.container('gpu-0')
    existing = json.loads(run('podman', 'ps', '-a', '--format', 'json'))
    if any(name in item.get('Names', []) for item in existing):
        raise ValueError('GPU container already exists; inspect it rather than replace identity')
    node = root/'gpu-0'
    telemetry = root/'exchange/contributors/gpu-0'
    for path in (node, telemetry):
        path.mkdir(parents=True, exist_ok=True); os.chown(path, 1000, 1000)
    os.chmod(node, 0o700)
    image = json.loads(run('podman', 'image', 'inspect', 'localhost/dawl-gpu:node-v1'))[0]['Id']
    args = ['podman', 'run', '-d', '--name', name, '--network', 'host', '--cpus', '2', '--memory', '2g',
        '--pids-limit', '256', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--device', '/dev/dxg',
        '-v', '/usr/lib/wsl:/usr/lib/wsl:ro', '-v', str(node)+':/node',
        '-v', str(root/'exchange/bundle')+':/bootstrap:ro', '-v', str(telemetry)+':/telemetry',
        '-e', 'DAWL_IMAGE_PIN='+image, image, '--deadline', deadline,
        '--network', (root/'exchange/network.sha256').read_text().strip()]
    container = run(*args).decode().strip()
    save(root/'gpu-launched.json', dict(container=container, image=image, deadline=deadline,
        started_at=datetime.now(timezone.utc).isoformat(), name=name, cpu_limit=2, memory_limit_gb=2,
        gpu='WSL /dev/dxg; resident table + bounded output buffers; no cloud GPU'))
    print(container, flush=True)


if __name__ == '__main__': main()
