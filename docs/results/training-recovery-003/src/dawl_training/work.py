"""Executable contract behind the Oracle's data-only work catalog."""
import math
from importlib.metadata import version
import numpy as np
from dawl_profiles.profiles import content_id, descriptor
from .protocol import validate_layout


def model_contract(manifest):
    """Bind the meaning of adapter bytes across forks and continuation stages."""
    return content_id({'schema': 'hypertrix.adapter-model-contract/1', 'profile_id': manifest['profile']['id'],
                       'parent_model': manifest['parent_model']['sha256'], 'fixed_projection': manifest['fixed_projection'],
                       'projection_layout': manifest['projection_layout'], 'adapter_layout': manifest['adapter_layout'],
                       'dtype': 'float32', 'adapter': 'fixed-A-LoRA/rank8/alpha16/q_proj+v_proj'})


def catalog(dataset, rounds, shards, steps):
    width = 2*steps; result = []
    for index in range(rounds*shards):
        records = dataset['train'][index*width:(index+1)*width]
        if len(records) != width: raise ValueError('insufficient unique training windows')
        result.append({'round': index//shards, 'shard': index % shards, 'start': index*width,
                       'sequences': width, 'target_tokens': sum(r['count'] for r in records), 'data': content_id(records)})
    if len({content_id(r) for r in dataset['train'][:rounds*shards*width]}) != rounds*shards*width:
        raise ValueError('overlapping training windows')
    return result


def validate_manifest(manifest, stage, dataset):
    b = stage['body']; recipe = manifest['recipe']
    if 'branch' in b and b['model_contract'] != model_contract(manifest):
        raise ValueError('executable model structure differs from branch contract')
    if set(manifest) != {'schema', 'profile', 'parent_model', 'dataset', 'adapter_layout', 'initial_adapter',
                         'projection_layout', 'fixed_projection', 'recipe', 'runtime', 'upstream'}:
        raise ValueError('invalid executable manifest fields')
    if (manifest['schema'] != 'hypertrix.training-manifest/2' or manifest['profile']['id'] != b['profile_id']
            or manifest['parent_model']['sha256'] != b['parent_model'] or manifest['initial_adapter'] != b['initial_adapter']):
        raise ValueError('manifest differs from registered stage')
    expected = {'algorithm': 'fixed-A-LoRA/local-SGD/leased-token-weighted-mean/v1',
                'optimizer': 'SGD/no-momentum/no-weight-decay', 'dtype': 'float32', 'seed': 20261014,
                'rank': 8, 'alpha': 16, 'targets': ['q_proj', 'v_proj'], 'sequence_length': 128, 'batch_size': 2,
                'rounds': b['rounds'], 'local_steps': b['local_steps'], 'learning_rate': 0.1,
                'clip_grad_norm': 1.0, 'max_update_l2': 2.0}
    if recipe != expected: raise ValueError('unsupported execution recipe')
    for name in ('dataset', 'initial_adapter', 'fixed_projection'): descriptor(manifest[name])
    validate_layout(manifest['adapter_layout'], manifest['initial_adapter']['bytes'])
    validate_layout(manifest['projection_layout'], manifest['fixed_projection']['bytes'])
    if b['work'] != catalog(dataset, b['rounds'], b['shards'], b['local_steps']):
        raise ValueError('registered work differs from verified data windows')
    for library, expected_version in manifest['runtime'].items():
        if version(library) != expected_version: raise ValueError('training runtime differs: '+library)


def records(dataset, spec):
    values = dataset['train'][spec['start']:spec['start']+spec['sequences']]
    if content_id(values) != spec['data'] or sum(r['count'] for r in values) != spec['target_tokens']:
        raise ValueError('training shard differs from allocation')
    return values


def reduce_updates(objects, previous, jobs, maximum=2.0):
    """Canonical shard order, token-weighted FP64 summation, one FP32 rounding."""
    base = np.frombuffer(objects.read(previous), dtype='<f4')
    if not np.isfinite(base).all(): raise ValueError('nonfinite parent')
    if [j['spec']['shard'] for j in jobs] != list(range(len(jobs))): raise ValueError('noncanonical shard order')
    total = sum(j['spec']['target_tokens'] for j in jobs); result = np.zeros(len(base), dtype=np.float64)
    if not total or not math.isfinite(maximum) or maximum <= 0: raise ValueError('invalid reduction policy')
    for job in jobs:
        values = np.frombuffer(objects.read(job['contribution']['adapter']), dtype='<f4')
        if (len(values) != len(base) or not np.isfinite(values).all()
                or np.linalg.norm(values.astype(np.float64)-base) > maximum):
            raise ValueError('nonfinite or oversized contribution')
        result += values.astype(np.float64)*(job['spec']['target_tokens']/total)
    payload = result.astype('<f4').tobytes()
    if not np.isfinite(np.frombuffer(payload, dtype='<f4')).all(): raise ValueError('nonfinite aggregate')
    return payload
