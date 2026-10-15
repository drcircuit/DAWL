"""Measured local execution fingerprint; a declaration, not hardware attestation."""
from importlib.metadata import version
import os
import platform
import subprocess
import shutil
from pathlib import Path
from dawl_cluster.collaboration import identity, validate_environment, LIBRARIES
from dawl_node.common import sha256
from dawl_platform.workflow import implementation_digest


def capture(device='cuda:0'):
    import torch
    from .model import setup
    # Set before the first CUDA work; fail if the caller configured another policy.
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    if os.environ['CUBLAS_WORKSPACE_CONFIG'] != ':4096:8':
        raise ValueError('CUBLAS_WORKSPACE_CONFIG must be :4096:8')
    setup()
    target = torch.device(device)
    if target.type == 'cuda':
        if not torch.cuda.is_available(): raise ValueError('requested CUDA device unavailable')
        props = torch.cuda.get_device_properties(target)
        # The host driver is not captured by the torch/CUDA package version.
        smi = shutil.which('nvidia-smi')
        if not smi and Path('/usr/lib/wsl/lib/nvidia-smi').is_file(): smi = '/usr/lib/wsl/lib/nvidia-smi'
        if not smi: raise ValueError('nvidia-smi unavailable; cannot pin host driver')
        drivers = subprocess.check_output([smi, '--query-gpu=driver_version', '--format=csv,noheader'],
                                         text=True, timeout=10).strip().splitlines()
        if not drivers or len(set(drivers)) != 1: raise ValueError('ambiguous CUDA driver fingerprint')
        hardware = {'type': 'cuda', 'name': props.name, 'capability': f'{props.major}.{props.minor}', 'driver': drivers[0]}
    elif target.type == 'cpu':
        name = platform.processor()
        if platform.system() == 'Linux':
            for line in Path('/proc/cpuinfo').read_text().splitlines():
                if line.startswith('model name'): name = line.split(':', 1)[1].strip(); break
        if not name: raise ValueError('CPU model unavailable for strict fingerprint')
        hardware = {'type': 'cpu', 'name': name, 'capability': torch.backends.cpu.get_cpu_capability(), 'driver': 'not-applicable'}
    else: raise ValueError('unsupported execution device')
    body = {'schema': 'hypertrix.execution-environment/1', 'implementation_sha256': implementation_digest(),
            'runtime': {name: version(name) for name in sorted(LIBRARIES)},
            'python': platform.python_version(), 'system': platform.system(), 'machine': platform.machine(),
            'torch_build_sha256': sha256(torch.__config__.show().encode()), 'device': hardware,
            'execution': {'dtype': 'float32', 'attention': 'eager', 'compiler': 'eager/no-compile',
                          'tf32': False, 'deterministic': True, 'threads': 2, 'cublas_workspace_config': ':4096:8'}}
    value = {'id': identity(body), 'body': body}; validate_environment(value)
    return value


def check_stage(state, stage, public, config, measured):
    """Fail before data download/training if local pins or measured cohort differ."""
    from dawl_cluster.collaboration import context, require_member
    branch, manifest = context(state, stage['body']['branch'])
    if (config.get('collaboration_manifest') != manifest['id']
            or config.get('execution_environment') != measured['id']
            or branch['body']['environment'] != measured['id']
            or measured not in manifest['body']['environments']):
        raise ValueError('local execution environment or explicit participation pins differ from stage')
    require_member(state, branch, public)
    return measured['body']['runtime']
