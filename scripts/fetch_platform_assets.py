"""Fetch pinned, data-only GPT-2 weights and extract the real input embedding table."""
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import immutable_write, read_json, sha256, write_json

REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"
WEIGHTS_SHA = "248dfc3911869ec493c76e65bf2fcf7f615828b0254c12b473182f0f81d3a707"
WEIGHTS_BYTES = 548105171
ROOT = "https://huggingface.co/openai-community/gpt2/resolve/" + REVISION + "/"


def fetch(output=Path("artifacts/platform-assets")):
    from safetensors import safe_open
    output.mkdir(parents=True, exist_ok=True)
    weights = output / "model.safetensors"
    if not weights.exists():
        pending = output / "model.download"
        digest, size = hashlib.sha256(), 0
        with urlopen(ROOT + "model.safetensors", timeout=60) as response, pending.open("wb") as stream:
            while payload := response.read(1024 * 1024):
                size += len(payload)
                if size > WEIGHTS_BYTES:
                    raise ValueError("weights exceed the pinned size")
                digest.update(payload)
                stream.write(payload)
        if size != WEIGHTS_BYTES or digest.hexdigest() != WEIGHTS_SHA:
            raise ValueError("weights differ from pinned upstream SHA-256")
        pending.rename(weights)
    with weights.open("rb") as stream:
        if weights.stat().st_size != WEIGHTS_BYTES or hashlib.file_digest(stream, "sha256").hexdigest() != WEIGHTS_SHA:
            raise ValueError("cached weights differ from lock")
    with safe_open(weights, framework="numpy") as tensors:
        matrix = tensors.get_tensor("wte.weight")
    if matrix.shape != (50257, 768) or matrix.dtype.name != "float32":
        raise ValueError("unexpected GPT-2 embedding table")
    payload = matrix.astype("<f4", copy=False).tobytes(order="C")
    immutable_write(output / "embedding.f32le", payload)
    tokenizer = Path("artifacts/ingestion-assets/tokenizer.json").read_bytes()
    # Verify that the model revision still uses the exact same vocabulary and merges.
    with urlopen(ROOT + "tokenizer.json", timeout=30) as response:
        model_tokenizer = response.read(16 * 1024 * 1024)
    from tokenizers import Tokenizer
    import json
    a, b = [json.loads(Tokenizer.from_str(p.decode()).to_str()) for p in (tokenizer, model_tokenizer)]
    if a != b:
        raise ValueError("model tokenizer is incompatible with the pinned ingestion tokenizer")
    with urlopen(ROOT + "README.md", timeout=30) as response:
        card = response.read(1024 * 1024)
    immutable_write(output / "upstream-model-card.md", card)
    descriptor = {"schema": "dawl.embedding-table/1", "upstream": "openai-community/gpt2",
                  "revision": REVISION, "weights_sha256": WEIGHTS_SHA, "weights_bytes": WEIGHTS_BYTES,
                  "tensor": "wte.weight", "shape": [50257, 768], "dtype": "float32-le",
                  "sha256": sha256(payload), "bytes": len(payload), "tokenizer_sha256": sha256(tokenizer),
                  "model_card_sha256": sha256(card), "upstream_license_declaration": "MIT",
                  "contract": "token-input-embedding-lookup/v1; no positions, contextual layers, pooling or rounding",
                  "lineage_boundary": "Imported pretrained weights; upstream training history is not verified by DAWL."}
    write_json(output / "embedding.json", descriptor)
    print(descriptor, flush=True)
    return descriptor


if __name__ == "__main__":
    fetch()
