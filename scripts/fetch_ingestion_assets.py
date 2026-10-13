"""Fetch a version-pinned tokenizer and a public text fixture; never model weights."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

ASSETS = {
    "tokenizer.json": "https://huggingface.co/openai-community/gpt2/resolve/210ab4c33a01af45a1914b1cf1928eb915565697/tokenizer.json",
    "alice.txt": "https://www.gutenberg.org/cache/epub/11/pg11.txt",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/ingestion-assets"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock_path = args.output / "assets.lock.json"
    existing = json.loads(lock_path.read_bytes()) if lock_path.exists() else None
    lock = {}
    for name, url in ASSETS.items():
        path = args.output / name
        if not path.exists():
            with urlopen(url, timeout=30) as response:
                payload = response.read(16 * 1024 * 1024 + 1)
            if len(payload) > 16 * 1024 * 1024:
                raise ValueError("asset exceeds download size limit")
            digest = hashlib.sha256(payload).hexdigest()
            if existing and existing[name]["sha256"] != digest:
                raise ValueError("download no longer matches the recorded asset")
            with path.open("xb") as stream:
                stream.write(payload)
        payload = path.read_bytes()
        lock[name] = {"url": url, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        if existing and existing[name] != lock[name]:
            raise ValueError(f"cached asset does not match its lock: {name}")
    if not existing:
        with lock_path.open("x", encoding="utf-8") as stream:
            json.dump(lock, stream, indent=2)
    print(json.dumps(lock, indent=2))


if __name__ == "__main__":
    main()
