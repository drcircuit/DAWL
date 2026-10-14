"""Fetch pinned, data-only tokenizer/config files for local profile evaluation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    'qwen3': ('Qwen/Qwen3-0.6B', 'c1899de289a04d12100db370d81485cdf75e47ca'),
    'qwen35': ('Qwen/Qwen3.5-0.8B', '2fc06364715b967f1860aea9cf38778875588b17'),
    'smollm2': ('HuggingFaceTB/SmolLM2-135M', '93efa2f097d58c2a74874c7e644dbc9b0cee75a2'),
    'smollm3': ('HuggingFaceTB/SmolLM3-3B-Base', 'd78a42f79198603e614095753484a04c10c2b940'),
}


def fetch(item):
    name, (repo, revision) = item
    output = ROOT / 'artifacts/tokenizer-candidates' / name
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / 'download-lock.json'
    prior = json.loads(lock_path.read_bytes()) if lock_path.exists() else None
    files = {}
    for filename in ('tokenizer.json', 'tokenizer_config.json', 'config.json', 'README.md'):
        path = output / filename
        url = f'https://huggingface.co/{repo}/resolve/{revision}/{filename}'
        if not path.exists():
            with urlopen(url, timeout=60) as response:
                payload = response.read(64 * 1024 * 1024 + 1)
            if len(payload) > 64 * 1024 * 1024:
                raise ValueError('bounded data download exceeded')
            path.write_bytes(payload)
        data = path.read_bytes()
        files[filename] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'url': url}
    lock = {'repo': repo, 'revision': revision, 'files': files,
            'scope': 'Data-only candidate acquisition. No model weights or remote Python code.'}
    if prior and lock != prior:
        raise ValueError('candidate cache differs from its recorded acquisition hashes')
    lock_path.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    config = json.loads((output / 'config.json').read_bytes())
    definition = json.loads((output / 'tokenizer.json').read_bytes())
    text_config = config.get('text_config', config)
    return {'name': name, 'vocab_size': text_config['vocab_size'], 'hidden_size': text_config['hidden_size'],
            'normalizer': definition.get('normalizer'), 'pre_tokenizer': definition.get('pre_tokenizer'),
            'tokenizer_bytes': files['tokenizer.json']['bytes']}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch, CANDIDATES.items()):
            print(json.dumps(result), flush=True)
