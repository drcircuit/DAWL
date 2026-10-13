"""Create the real-tokenizer fixture and distinct role identities entirely on CPU."""

import argparse
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, immutable_write, keygen, read_json, sha256, write_json
from dawl_node.source import register
from dawl_node.store import mine
from dawl_node.tokenizer import PinnedTokenizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=Path("artifacts/ingestion-assets"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/ingestion-demo"))
    args = parser.parse_args()
    lock = read_json(args.assets / "assets.lock.json")
    for name, entry in lock.items():
        if sha256((args.assets / name).read_bytes()) != entry["sha256"]:
            raise ValueError(f"asset failed its recorded hash: {name}")
    args.output.mkdir(parents=True, exist_ok=True)
    for role in ("miner", "validator"):
        identity = args.output / f"{role}-identity"
        if not (identity / "key.private.pem").exists():
            keygen(identity / "key.private.pem", identity / "public.key")
            immutable_write(identity / "api.token", (secrets.token_urlsafe(32) + "\n").encode("ascii"))
    text = (args.assets / "alice.txt").read_bytes().decode("utf-8")
    doc = {"id": "gutenberg:11", "text": text, "source_uri": lock["alice.txt"]["url"],
           "license": "Project Gutenberg License; catalogue labels the underlying work public domain in the USA"}
    source = args.output / "source.jsonl"
    immutable_write(source, canonical_json(doc) + b"\n")
    trust = args.output / "trust"
    trust.mkdir(exist_ok=True)
    registration = register(source, trust / "source.json", chunk_bytes=4096)
    immutable_write(trust / "tokenizer.json", (args.assets / "tokenizer.json").read_bytes())
    immutable_write(trust / "miner-public.key", (args.output / "miner-identity" / "public.key").read_bytes())
    tokenizer = PinnedTokenizer(trust / "tokenizer.json", lock["tokenizer.json"]["sha256"])
    result = mine(source, trust / "source.json", tokenizer, args.output / "miner-identity" / "key.private.pem",
                  args.output / "miner", shard_tokens=16384)
    result["tokenizer"] = tokenizer.descriptor
    result["assets"] = lock
    # Timings change on a repeat; the snapshot remains immutable and identical.
    (args.output / "run.json").write_bytes(canonical_json(result))
    immutable_write(args.output / "deploy.env", (
        f"DAWL_REGISTRY_ID={registration['registry_id']}\n"
        f"DAWL_TOKENIZER_SHA256={tokenizer.sha256}\n"
    ).encode("ascii"))
    (args.output / "receipts").mkdir(exist_ok=True)
    print(canonical_json(result).decode("ascii"))


if __name__ == "__main__":
    main()
