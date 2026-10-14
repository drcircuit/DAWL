"""Acquire the pinned public o200k_base ranks, never model weights or API calls."""
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from dawl_node.common import immutable_write, canonical_json, sha256
from dawl_profiles.o200k import RANKS_URL, RANKS_SHA256
from dawl_profiles.profiles import make_o200k_profile, describe


def main():
    root = ROOT/'artifacts/tokenizer-candidates/o200k_base'
    path = root/'o200k_base.tiktoken'
    if path.exists(): payload = path.read_bytes()
    else:
        with urlopen(RANKS_URL, timeout=60) as response: payload = response.read(8*1024*1024+1)
    if sha256(payload) != RANKS_SHA256: raise ValueError('upstream rank hash mismatch')
    profile = make_o200k_profile(payload)
    immutable_write(path, payload)
    immutable_write(root/'profile.json', canonical_json(profile))
    immutable_write(root/'download-lock.json', canonical_json({'url': RANKS_URL, 'artifact': describe(payload),
        'definition': 'https://github.com/openai/tiktoken/blob/0.14.0/tiktoken_ext/openai_public.py',
        'runtime': profile['body']['tokenizer']['version'], 'profile_id': profile['id']}))
    print(canonical_json(profile).decode())


if __name__ == '__main__': main()
