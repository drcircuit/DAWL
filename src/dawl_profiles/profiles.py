"""Immutable tokenizer/model contracts and named, chunked weight manifests.

Data-only loading: never import upstream model code. These contracts do not
activate a consensus upgrade or claim that imported weights have been trained here.
"""
import math
import re
from importlib.metadata import version

from tokenizers import Tokenizer
from dawl_node.common import canonical_json, parse_json, sha256
from dawl_platform.artifacts import MAX_OBJECT
from data_ingestion.protocol import require_digest
from . import o200k

CONTRACT = 'independent-utf8-frags/no-auto-special-tokens/literal-added-tokens/v1'
DTYPES = {'float32-le': 4, 'float16-le': 2, 'bfloat16-le': 2}


def descriptor(value):
    if not isinstance(value, dict) or set(value) != {'sha256', 'bytes'}:
        raise ValueError('invalid artifact descriptor')
    require_digest(value['sha256'])
    if type(value['bytes']) is not int or not 0 <= value['bytes'] <= MAX_OBJECT:
        raise ValueError('artifact size outside bound')


def describe(payload):
    return {'sha256': sha256(payload), 'bytes': len(payload)}


def content_id(value):
    return sha256(canonical_json(value))


def make_profile(tokenizer_bytes, config_bytes, embedding_name, label):
    core = Tokenizer.from_str(tokenizer_bytes.decode('utf-8'))
    config = parse_json(config_bytes)
    text = config.get('text_config', config)
    body = {
        'schema': 'hypertrix.model-profile/1', 'label': label,
        'tokenizer': {'artifact': describe(tokenizer_bytes), 'library': 'tokenizers',
                      'version': version('tokenizers'), 'contract': CONTRACT},
        'model': {'config': describe(config_bytes), 'family': config.get('model_type', 'gpt2'),
                  'input_embedding': {'name': embedding_name,
                      'shape': [text['vocab_size'], text.get('hidden_size', text.get('n_embd'))]}},
        'chunking': {'part_bytes': 524288, 'frag_bytes': 4096, 'boundary': 'utf8-codepoint/v1'},
    }
    normalizer = parse_json(core.to_str().encode())['normalizer']
    body['tokenizer']['normalization'] = 'none' if normalizer is None else normalizer.get('type')
    profile = {'id': content_id(body), 'body': body}
    ProfileTokenizer(profile, tokenizer_bytes, config_bytes)
    return profile


def make_o200k_profile(payload):
    body = {'schema': 'hypertrix.tokenizer-profile/1', 'label': 'o200k_base',
            'tokenizer': {'artifact': describe(payload), 'library': 'tiktoken',
                          'version': version('tiktoken'), 'contract': o200k.CONTRACT,
                          'normalization': 'none'}, 'model': None,
            'chunking': {'part_bytes': 524288, 'frag_bytes': 4096, 'boundary': 'utf8-codepoint/v1'}}
    profile = {'id': content_id(body), 'body': body}
    ProfileTokenizer(profile, payload, None)
    return profile


def bind_o200k_model(source, payload, config_bytes, embedding_name, label):
    """New immutable model namespace over an unchanged ordinary-text tokenizer."""
    if source != make_o200k_profile(payload):
        raise ValueError('expected the canonical o200k tokenizer-only source profile')
    config = parse_json(config_bytes)
    body = {**source['body'], 'schema': 'hypertrix.model-profile/1', 'label': label,
            'model': {'config': describe(config_bytes), 'family': config['model_type'],
                'input_embedding': {'name': embedding_name, 'shape': [config['vocab_size'], config['hidden_size']]}}}
    profile = {'id': content_id(body), 'body': body}
    ProfileTokenizer(profile, payload, config_bytes)
    return profile


def validate_profile(profile):
    try:
        body = profile['body']
        if set(profile) != {'id', 'body'} or profile['id'] != content_id(body):
            raise ValueError('profile content hash mismatch')
        if set(body) != {'schema', 'label', 'tokenizer', 'model', 'chunking'} or body['schema'] not in ('hypertrix.model-profile/1', 'hypertrix.tokenizer-profile/1'):
            raise ValueError('unsupported profile schema')
        if not isinstance(body['label'], str) or not 1 <= len(body['label']) <= 160:
            raise ValueError('invalid profile label')
        tok, model = body['tokenizer'], body['model']
        if set(tok) != {'artifact', 'library', 'version', 'contract', 'normalization'}:
            raise ValueError('invalid tokenizer profile')
        descriptor(tok['artifact'])
        if body['chunking'] != {'part_bytes': 524288, 'frag_bytes': 4096, 'boundary': 'utf8-codepoint/v1'}:
            raise ValueError('unsupported source partition recipe')
        if not isinstance(tok['version'], str) or not 1 <= len(tok['version']) <= 40:
            raise ValueError('invalid tokenizer runtime pin')
        if body['schema'] == 'hypertrix.tokenizer-profile/1':
            if (model is not None or tok['library'] != 'tiktoken' or tok['contract'] != o200k.CONTRACT
                    or tok['normalization'] != 'none' or tok['artifact']['sha256'] != o200k.RANKS_SHA256):
                raise ValueError('unsupported tokenizer-only profile')
            return body
        descriptor(model['config'])
        hf = tok['library'] == 'tokenizers' and tok['contract'] == CONTRACT and tok['normalization'] in ('none', 'NFC')
        base = (tok['library'] == 'tiktoken' and tok['contract'] == o200k.CONTRACT
                and tok['normalization'] == 'none' and tok['artifact']['sha256'] == o200k.RANKS_SHA256)
        if not (hf or base):
            raise ValueError('unsupported tokenization contract')
        if not isinstance(tok['version'], str) or not 1 <= len(tok['version']) <= 40:
            raise ValueError('invalid tokenizer runtime pin')
        if set(model) != {'config', 'family', 'input_embedding'} or not isinstance(model['family'], str):
            raise ValueError('invalid model profile')
        emb = model['input_embedding']
        if set(emb) != {'name', 'shape'} or not re.fullmatch(r'[A-Za-z0-9_.]{1,200}', emb['name']):
            raise ValueError('invalid embedding tensor identity')
        shape = emb['shape']
        if not isinstance(shape, list) or len(shape) != 2 or any(type(n) is not int or not 1 <= n <= 1_000_000 for n in shape):
            raise ValueError('invalid embedding shape')
        if base and shape[0] < 200019:
            raise ValueError('model input table cannot cover o200k token ID space')
        if body['chunking'] != {'part_bytes': 524288, 'frag_bytes': 4096, 'boundary': 'utf8-codepoint/v1'}:
            raise ValueError('unsupported source partition recipe')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('malformed profile') from error
    return body


class ProfileTokenizer:
    def __init__(self, profile, payload, config_payload=None):
        body = validate_profile(profile)
        tok = body['tokenizer']; self.profile_id = profile['id']
        if describe(payload) != tok['artifact']:
            raise ValueError('tokenizer differs from profile')
        self.library = tok['library']
        if tok['version'] != version(self.library):
            raise ValueError('tokenizer runtime differs from profile')
        if self.library == 'tiktoken':
            self.core = o200k.encoding(payload)
            self.rows, self.dimension = self.core.n_vocab, None
            self.vocab_size = len(self.core.token_byte_values()) + len(o200k.SPECIAL_TOKENS)
            if body['model'] is None:
                if config_payload is not None: raise ValueError('tokenizer-only profile has no model config')
            else:
                model = body['model']
                if describe(config_payload) != model['config']: raise ValueError('model config differs from profile')
                config = parse_json(config_payload)
                self.rows, self.dimension = model['input_embedding']['shape']
                if ([self.rows, self.dimension] != [config['vocab_size'], config['hidden_size']]
                        or self.rows < self.core.n_vocab or model['family'] != config['model_type']):
                    raise ValueError('model config cannot cover o200k token ID space')
            return
        if describe(config_payload) != body['model']['config']:
            raise ValueError('tokenizer or model config differs from profile')
        if tok['version'] != version('tokenizers'):
            raise ValueError('tokenizer runtime differs from profile')
        self.core = Tokenizer.from_str(payload.decode('utf-8'))
        definition = parse_json(self.core.to_str().encode('utf-8'))
        model = definition['model']
        norm = definition['normalizer']
        expected_norm = None if tok['normalization'] == 'none' else {'type': 'NFC'}
        if (model['type'] != 'BPE' or model.get('dropout') not in (None, 0)
                or norm != expected_norm or definition.get('padding') is not None
                or definition.get('truncation') is not None
                or definition['decoder'].get('type') != 'ByteLevel'):
            raise ValueError('unsupported or stochastic tokenizer configuration')
        def check_pre(pre):
            kind = pre.get('type')
            if kind == 'Sequence':
                for step in pre['pretokenizers']: check_pre(step)
            elif kind == 'ByteLevel':
                if pre.get('add_prefix_space') is not False: raise ValueError('prefix insertion unsupported')
            elif kind not in ('Split', 'Digits'):
                raise ValueError('unsupported pre-tokenizer')
        check_pre(definition['pre_tokenizer'])
        self.rows, self.dimension = body['model']['input_embedding']['shape']
        vocab = self.core.get_vocab(with_added_tokens=True)
        if not vocab or max(vocab.values()) >= self.rows:
            raise ValueError('token ID exceeds model embedding rows')
        self.vocab_size = len(vocab)
        config = parse_json(config_payload)
        text_config = config.get('text_config', config)
        shape = [text_config['vocab_size'], text_config.get('hidden_size', text_config.get('n_embd'))]
        if shape != [self.rows, self.dimension]: raise ValueError('model config shape differs from profile')

    def encode(self, text):
        text.encode('utf-8', errors='strict')
        if self.library == 'tiktoken':
            ids = self.core.encode_ordinary(text)
            if self.core.decode_bytes(ids) != text.encode('utf-8'):
                raise ValueError('o200k tokenization does not round-trip raw text')
            return text, ids
        normalized = self.core.normalizer.normalize_str(text) if self.core.normalizer else text
        ids = self.core.encode(text, add_special_tokens=False).ids
        if self.core.decode(ids, skip_special_tokens=False) != normalized:
            raise ValueError('tokenizer does not round-trip its declared normalized text')
        if any(type(i) is not int or not 0 <= i < self.rows for i in ids):
            raise ValueError('token ID exceeds profile embedding rows')
        return normalized, ids


def validate_weights(manifest, profile, objects=None):
    """Validate arbitrary named dense tensors, each split into bounded CAS objects.

    Byte/layout integrity only. No training claim or numerical execution implied.
    Quantized/sparse/custom layouts require a new explicit contract.
    """
    body = validate_profile(profile)
    if body['model'] is None:
        raise ValueError('tokenizer-only profile cannot bind model weights')
    try:
        if (set(manifest) != {'schema', 'profile_id', 'tensors'} or
                manifest['schema'] != 'hypertrix.named-dense-weights/1' or manifest['profile_id'] != profile['id']):
            raise ValueError('weight manifest belongs to another profile')
        tensors = manifest['tensors']
        if not isinstance(tensors, dict) or not 1 <= len(tensors) <= 16384:
            raise ValueError('invalid tensor count')
        for name, tensor in tensors.items():
            if not re.fullmatch(r'[A-Za-z0-9_.]{1,200}', name) or set(tensor) != {'shape', 'dtype', 'chunks'}:
                raise ValueError('invalid tensor layout')
            shape = tensor['shape']
            if (not isinstance(shape, list) or not 1 <= len(shape) <= 8 or
                    any(type(n) is not int or not 1 <= n <= 2**31 for n in shape) or tensor['dtype'] not in DTYPES):
                raise ValueError('unsupported tensor shape or dtype')
            if not isinstance(tensor['chunks'], list) or not 1 <= len(tensor['chunks']) <= 65536:
                raise ValueError('invalid tensor chunks')
            for chunk in tensor['chunks']:
                descriptor(chunk)
                if objects is not None: objects.read(chunk)
            if sum(c['bytes'] for c in tensor['chunks']) != math.prod(shape) * DTYPES[tensor['dtype']]:
                raise ValueError('tensor byte length differs from shape and dtype')
        emb = body['model']['input_embedding']
        if tensors[emb['name']]['shape'] != emb['shape']:
            raise ValueError('input embedding layout differs from profile')
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('malformed weight manifest') from error
    return content_id(manifest)
