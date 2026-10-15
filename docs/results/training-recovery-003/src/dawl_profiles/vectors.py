"""Profile-bound input-table imports and exact reconstruction commitments.

Finite F32/F16/BF16 input weights become canonical little-endian F32 rows. This
does not execute transformer layers or establish upstream training provenance.
"""
import hashlib
import math
from pathlib import Path
import struct
import numpy as np

from dawl_node.common import parse_json, sha256, verify_signature
from data_ingestion.protocol import require_digest
from .profiles import content_id, validate_weights, descriptor

CONTRACT = 'hypertrix.input-gather/f32le-v1; finite F32 identity, F16 exact widen, BF16 bits<<16; row lookup only'
MAX_TABLE = 4 * 1024**3
CHUNK = 32 * 1024**2


def safetensor(path, expected, name, shape):
    """Verify the complete upstream shard before accepting offsets from its header."""
    path = Path(path)
    if path.stat().st_size != expected['bytes']: raise ValueError('checkpoint size mismatch')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected['sha256']:
            raise ValueError('checkpoint hash mismatch')
        stream.seek(0); prefix = stream.read(8)
        if len(prefix) != 8: raise ValueError('truncated safetensors header')
        length = struct.unpack('<Q', prefix)[0]
        if not 2 <= length <= 16*1024**2: raise ValueError('invalid safetensors header bound')
        header = parse_json(stream.read(length), 16*1024**2)
    if name not in header: raise ValueError('input embedding missing from shard')
    tensor = header[name]
    if set(tensor) != {'dtype', 'shape', 'data_offsets'} or tensor['shape'] != shape:
        raise ValueError('upstream input embedding shape mismatch')
    size = {'F32': 4, 'F16': 2, 'BF16': 2}.get(tensor['dtype'])
    if size is None or len(shape) != 2 or any(type(n) is not int or n <= 0 for n in shape):
        raise ValueError('unsupported input tensor')
    if math.prod(shape)*4 > MAX_TABLE: raise ValueError('input table exceeds bound')
    offsets = tensor['data_offsets']
    if (not isinstance(offsets, list) or len(offsets) != 2 or any(type(n) is not int for n in offsets)
            or offsets[0] < 0 or offsets[1]-offsets[0] != math.prod(shape)*size
            or 8+length+offsets[1] > expected['bytes']):
        raise ValueError('invalid safetensors tensor offsets')
    return tensor, 8+length+offsets[0], content_id(header)


def widen(payload, dtype):
    if dtype == 'F32': values = np.frombuffer(payload, dtype='<f4')
    elif dtype == 'F16': values = np.frombuffer(payload, dtype='<f2').astype('<f4')
    elif dtype == 'BF16': values = (np.frombuffer(payload, dtype='<u2').astype('<u4') << 16).view('<f4')
    else: raise ValueError('unsupported input dtype')
    if not np.isfinite(values).all(): raise ValueError('nonfinite input embedding')
    return values.tobytes()


def import_embedding(objects, profile, path, upstream):
    model = profile['body']['model']
    if model is None: raise ValueError('tokenizer-only profile has no input embedding')
    emb = model['input_embedding']
    tensor, offset, header_id = safetensor(path, upstream, upstream['tensor'], emb['shape'])
    chunks = []; sha = hashlib.sha256(); remaining = tensor['data_offsets'][1]-tensor['data_offsets'][0]
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        while remaining:
            raw = stream.read(min(CHUNK if tensor['dtype'] == 'F32' else CHUNK//2, remaining))
            if not raw: raise ValueError('truncated tensor data')
            remaining -= len(raw); data = widen(raw, tensor['dtype']); sha.update(data); chunks.append(objects.put(data))
    manifest = {'schema': 'hypertrix.named-dense-weights/1', 'profile_id': profile['id'],
                'tensors': {emb['name']: {'shape': emb['shape'], 'dtype': 'float32-le', 'chunks': chunks}}}
    mid = validate_weights(manifest, profile, objects)
    binding = {'schema': 'hypertrix.input-table-import/1', 'profile_id': profile['id'],
               'weights_id': mid, 'weights': manifest, 'contract': CONTRACT,
               'table': {'sha256': sha.hexdigest(), 'bytes': math.prod(emb['shape'])*4},
               'upstream': upstream, 'upstream_tensor': tensor, 'upstream_header_id': header_id,
               'training_history_verified': False, 'scope': 'Imported input embedding only; other model layers not imported.'}
    return binding


def materialize(objects, profile, binding, path):
    if (binding.get('schema') != 'hypertrix.input-table-import/1' or binding.get('contract') != CONTRACT
            or binding.get('profile_id') != profile['id']): raise ValueError('wrong input table binding')
    manifest = binding['weights']
    if validate_weights(manifest, profile) != binding['weights_id']: raise ValueError('weight manifest ID mismatch')
    emb = profile['body']['model']['input_embedding']; tensor = manifest['tensors'][emb['name']]
    if tensor['dtype'] != 'float32-le' or set(manifest['tensors']) != {emb['name']}:
        raise ValueError('input gather needs exactly one canonical F32 tensor')
    expected = binding['table']; require_digest(expected['sha256'])
    # A table can exceed the individual CAS object cap because it has bounded chunks.
    if (set(expected) != {'sha256', 'bytes'} or type(expected['bytes']) is not int
            or expected['bytes'] != math.prod(emb['shape'])*4 or expected['bytes'] > MAX_TABLE):
        raise ValueError('wrong table length')
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    with path.open('wb') as stream:
        for chunk in tensor['chunks']:
            data = objects.read(chunk); sha.update(data); stream.write(data)
    if sha.hexdigest() != expected['sha256']: raise ValueError('assembled table hash mismatch')
    table = np.memmap(path, mode='r', dtype='<f4', shape=tuple(emb['shape']))
    # Check in bounded row windows rather than allocating a table-sized mask.
    for start in range(0, len(table), 4096):
        if not np.isfinite(table[start:start+4096]).all(): raise ValueError('nonfinite canonical table')
    return table


def validate_commitment(envelope, public, profile, blueprint, binding, source_profile=None):
    body = verify_signature(envelope, public)
    fields = {'schema','signer','profile_id','token_blueprint','table_import','contract','books'}
    schema = 'hypertrix.profile-vectors/1'
    if source_profile is not None:
        fields.add('source_profile_id'); schema = 'hypertrix.profile-vectors/2'
        if (body.get('source_profile_id') != source_profile['id']
                or binding.get('source_profile_id') != source_profile['id']
                or binding.get('execution_profile') != profile):
            raise ValueError('source-to-execution profile binding mismatch')
    if (set(body) != fields
            or body['schema'] != schema or body['contract'] != CONTRACT
            or body['profile_id'] != profile['id'] or body['token_blueprint'] != blueprint
            or body['table_import'] != content_id(binding)):
        raise ValueError('vector commitment binding mismatch')
    if not isinstance(body['books'], list) or not 1 <= len(body['books']) <= 32:
        raise ValueError('invalid vector book count')
    return body


def verify_frag(actual, expected_cpu, record, token_record, width):
    if (set(record) != {'index','tokens','count','vectors'} or
            any(record[k] != token_record[k] for k in ('index','tokens','count'))):
        raise ValueError('vector commitment token binding mismatch')
    descriptor(record['vectors'])
    if (actual != expected_cpu or len(actual) != token_record['count']*width*4
            or record['vectors'] != {'sha256':sha256(actual),'bytes':len(actual)}):
        raise ValueError('vector commitment mismatch')
