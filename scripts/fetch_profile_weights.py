"""Acquire only embedding-containing safetensors shards, pinned by full-file SHA.

No pickle, remote Python or model execution. Other layers in a required shard are
downloaded to verify its published SHA; only the input tensor is later imported.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    'gpt-oss-20b': ('model-00002-of-00002.safetensors', 4170342232, 'a18106b209e9ab35c3406db4f6f12a927364a058b21e9d1373d682e20674b303', 'model.embed_tokens.weight'),
    'smollm2': ('model.safetensors', 269060552, '80521b40281d6ce74e35c9282c22539e75aa0ac8578892b2a59955ef78d55da1', 'model.embed_tokens.weight'),
    'qwen3': ('model.safetensors', 1503300328, 'f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b', 'model.embed_tokens.weight'),
    'qwen35': ('model.safetensors-00001-of-00001.safetensors', 1746942600, '04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696', 'model.language_model.embed_tokens.weight'),
    'smollm3': ('model-00001-of-00002.safetensors', 4966315264, '7e270ac568ee1880ddbadad66ccdcd9906d52415e8904e2f300c75250b9c7d49', 'model.embed_tokens.weight'),
}


def fetch(name):
    filename, size, digest, tensor = FILES[name]
    folder = ROOT/'artifacts/profile-weights'/name
    folder.mkdir(parents=True, exist_ok=True)
    upstream = json.loads((ROOT/'artifacts/tokenizer-candidates'/name/'download-lock.json').read_bytes())
    url = f"https://huggingface.co/{upstream['repo']}/resolve/{upstream['revision']}/{filename}"
    target = folder/filename
    start = time.perf_counter()
    if not target.exists():
        temporary = target.with_suffix('.partial')
        total = 0; sha = hashlib.sha256()
        with httpx.stream('GET', url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            with temporary.open('wb') as stream:
                for data in response.iter_bytes(4*1024*1024):
                    total += len(data)
                    if total > size: raise ValueError('download exceeds pinned size')
                    sha.update(data); stream.write(data)
        if total != size or sha.hexdigest() != digest: raise ValueError('checkpoint differs from pinned SHA/size')
        temporary.rename(target)
    else:
        with target.open('rb') as stream: actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if target.stat().st_size != size or actual != digest: raise ValueError('cached checkpoint differs from pin')
    lock = dict(repo=upstream['repo'], revision=upstream['revision'], filename=filename,
                sha256=digest, bytes=size, tensor=tensor, url=url,
                scope='Full embedding-containing shard verified; upstream training provenance unverified.')
    lock_path = folder/'download-lock.json'
    if lock_path.exists() and json.loads(lock_path.read_bytes()) != lock: raise ValueError('acquisition lock changed')
    lock_path.write_text(json.dumps(lock, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(name=name, bytes=size, sha256=digest, seconds=time.perf_counter()-start, verified=True)), flush=True)


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(fetch, FILES))
