"""Offline o200k_base contract, following openai/tiktoken's encoding definition.

The upstream ranks are MIT licensed. Literal control strings are ordinary text;
no chat template, special-token insertion or Unicode normalization is applied.
"""
import base64

RANKS_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'
RANKS_SHA256 = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
CONTRACT = 'o200k_base/independent-utf8-frags/encode-ordinary/v1'
PATTERN = '|'.join([
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
    r'\p{N}{1,3}', r' ?[^\s\p{L}\p{N}]+[\r\n/]*',
    r'\s*[\r\n]+', r'\s+(?!\S)', r'\s+',
])
SPECIAL_TOKENS = {'<|endoftext|>': 199999, '<|endofprompt|>': 200018}


def encoding(payload):
    from tiktoken import Encoding
    from dawl_node.common import sha256
    if sha256(payload) != RANKS_SHA256:
        raise ValueError('o200k_base rank file differs from upstream pin')
    ranks = {base64.b64decode(token, validate=True): int(rank)
             for token, rank in (line.split() for line in payload.splitlines() if line)}
    return Encoding('o200k_base', pat_str=PATTERN, mergeable_ranks=ranks,
                    special_tokens=SPECIAL_TOKENS)
