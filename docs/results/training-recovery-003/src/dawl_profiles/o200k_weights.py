"""Verify o200k ordinary-token compatibility with a pinned GPT-OSS tokenizer.

No Harmony message parsing/insertion. Literal control strings remain ordinary
text. Compatibility is restricted to input rows, not full inference semantics.
"""
from tokenizers import Tokenizer
from dawl_node.common import canonical_json, parse_json, unpack_ids
from .o200k import encoding, PATTERN
from .profiles import ProfileTokenizer, describe, validate_profile

CONTRACT = 'o200k_base-to-gpt-oss/ordinary-input-rows/v1'


def verify_compatibility(source, target, ranks, config, upstream_tokenizer):
    validate_profile(source); validate_profile(target)
    if (source['body']['model'] is not None or target['body']['model']['family'] != 'gpt_oss'
            or source['body']['tokenizer'] != target['body']['tokenizer']
            or source['body']['chunking'] != target['body']['chunking']):
        raise ValueError('incompatible source and model profiles')
    tokenizer = ProfileTokenizer(target, ranks, config)
    definition = parse_json(upstream_tokenizer)
    expected_pre = {'type':'Sequence','pretokenizers':[
        {'type':'Split','pattern':{'Regex':PATTERN},'behavior':'Isolated','invert':False},
        {'type':'ByteLevel','add_prefix_space':False,'trim_offsets':True,'use_regex':False}]}
    if (definition['normalizer'] is not None or definition['pre_tokenizer'] != expected_pre
            or definition['decoder']['type'] != 'ByteLevel' or definition['model']['type'] != 'BPE'
            or definition['model'].get('dropout') not in (None, 0)
            or definition['padding'] is not None or definition['truncation'] is not None):
        raise ValueError('upstream ordinary encoding contract differs')
    # Invert the standard ByteLevel alphabet, rather than equating Unicode token
    # strings with their raw UTF-8 encoding. All 199,998 ordinary mappings are checked.
    byte_values = list(range(33,127)) + list(range(161,173)) + list(range(174,256))
    characters = byte_values.copy(); extra = 0
    for byte in range(256):
        if byte not in byte_values:
            byte_values.append(byte); characters.append(256+extra); extra += 1
    alphabet = {b:chr(c) for b,c in zip(byte_values,characters)}
    ranks_map = tokenizer.core._mergeable_ranks
    vocab = definition['model']['vocab']
    for raw, token in ranks_map.items():
        if vocab.get(''.join(alphabet[b] for b in raw)) != token:
            raise ValueError('upstream ordinary token-to-row mapping differs')
    if max(vocab.values()) >= tokenizer.rows:
        raise ValueError('upstream vocabulary exceeds input table')
    # Removing added-token matching makes this a strict ordinary-text reference.
    # It must not recognize literal Harmony marker strings as control tokens.
    definition['added_tokens'] = []; definition['post_processor'] = None
    reference = Tokenizer.from_str(canonical_json(definition).decode())
    report = {'contract':CONTRACT,'source_profile_id':source['id'],'execution_profile_id':target['id'],
        'rank_artifact':describe(ranks),'model_config':describe(config),
        'upstream_tokenizer':describe(upstream_tokenizer),'ordinary_rows_verified':len(ranks_map),
        'max_ordinary_token_id':max(ranks_map.values()), 'model_rows':tokenizer.rows,
        'dimension':tokenizer.dimension,'special_token_policy':'Literal strings encoded as ordinary text; no Harmony rendering.'}
    return report, reference


def verify_corpus_compatibility(objects, entries, reference):
    count = tokens = 0
    for entry in entries:
        manifest = objects.read_json(entry['body']['payload']['data']['manifest'])
        for record in manifest['records']:
            text = objects.read(record['normalized']).decode('utf-8')
            actual = reference.encode(text, add_special_tokens=False).ids
            if actual != unpack_ids(objects.read(record['tokens'])):
                raise ValueError('upstream ordinary corpus encoding differs')
            count += 1; tokens += len(actual)
    return {'frags':count,'tokens':tokens,'exact_ordinary_encoding_match':True}
