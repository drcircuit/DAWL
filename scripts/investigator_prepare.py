"""Download a revision-pinned NLI evaluator; only data and safetensors are allowed."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

MODEL = 'MoritzLaurer/mDeBERTa-v3-base-mnli-xnli'
REVISION = '8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c'
FILES = ('README.md', 'config.json', 'model.safetensors', 'tokenizer.json',
         'tokenizer_config.json', 'special_tokens_map.json', 'added_tokens.json', 'spm.model')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    a.root.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name in FILES:
        target = a.root / name
        url = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}'
        if not target.exists():
            pending = a.root / (name + '.partial')
            with urllib.request.urlopen(url, timeout=60) as response, pending.open('wb') as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            pending.replace(target)
        with target.open('rb') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
        entries[name] = {'sha256': digest, 'bytes': target.stat().st_size, 'url': url}
        print(json.dumps({'file': name, **entries[name]}), flush=True)
    manifest = {'schema': 'hypertrix.investigator-model/1', 'repository': MODEL,
                'revision': REVISION, 'license': 'MIT (model card)', 'files': entries}
    payload = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    target = a.root / 'manifest.json'
    if target.exists() and target.read_bytes() != payload:
        raise ValueError('Existing model manifest differs')
    target.write_bytes(payload)
    print(json.dumps({'model_manifest_sha256': hashlib.sha256(payload).hexdigest()}), flush=True)


if __name__ == '__main__':
    main()
