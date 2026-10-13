"""Exercise already running role services from an independent local consumer."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import platform
import secrets
import sys
import time
from urllib.request import Request
from http.client import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, load_public, parse_json, read_json, verify_signature, write_json
from dawl_node.source import load_registry
from dawl_node.tokenizer import PinnedTokenizer
from dawl_node.transport import PeerClient, consume


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", type=Path, default=Path("artifacts/ingestion-demo"))
    parser.add_argument("--storage", default="http://127.0.0.1:8765")
    parser.add_argument("--validator", default="http://127.0.0.1:8766")
    parser.add_argument("--output", type=Path, default=Path("artifacts/ingestion-demo/deployment-check"))
    args = parser.parse_args()
    run = read_json(args.demo / "run.json")
    registry = load_registry(args.demo / "trust/source.json", run["registry_id"])
    tokenizer = PinnedTokenizer(args.demo / "trust/tokenizer.json", run["tokenizer"]["sha256"])
    miner = load_public(args.demo / "trust/miner-public.key")
    validator_public = load_public(args.demo / "validator-identity/public.key")
    if miner == validator_public:
        raise ValueError("miner and validator must have different signing identities")
    storage = PeerClient(args.storage, (args.demo / "miner-identity/api.token").read_text().strip())
    validator = PeerClient(args.validator, (args.demo / "validator-identity/api.token").read_text().strip())
    health = {}
    for role, peer in (("storage", storage), ("validator", validator)):
        for attempt in range(30):
            try:
                with peer.opener.open(Request(peer.base_url + "/health"), timeout=1) as response:
                    state = parse_json(response.read(1025), limit=1024)
                break
            except (ValueError, OSError, HTTPException):
                if attempt == 29:
                    raise
                time.sleep(1)
        if state["role"] != role or state["compute"] != "cpu":
            raise ValueError("unexpected role or compute mode")
        health[role] = state
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = []
    for count in sorted({min(8, run["chunks"]), min(256, run["chunks"])}):
        envelope = parse_json(validator.request("/v1/audits", {"snapshot_id": run["snapshot_id"], "samples": count}))
        result = verify_signature(envelope, validator_public)
        if (not result["accepted"] or result["snapshot_id"] != run["snapshot_id"]
                or result["source_registry"] != registry["id"] or result["checked_chunks"] != count):
            raise ValueError("validator did not accept the requested snapshot audit")
        write_json(args.output / f"receipt-{envelope['id']}.json", envelope)
        receipts.append({"receipt_id": envelope["id"], **result})
    # A fresh destination proves this invocation transfers all token shards.
    destination = args.output / ("consumer-" + secrets.token_hex(8))
    start = time.perf_counter()
    copied = consume(storage, run["snapshot_id"], registry, tokenizer, miner, destination)
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "consumer_platform": platform.platform(), "python": platform.python_version(),
              "storage_endpoint": args.storage, "validator_endpoint": args.validator,
              "health": health, "separate_signing_identities": True,
              "snapshot_id": run["snapshot_id"], "audits": receipts,
              "consumer": {**copied, "copy_seconds": time.perf_counter() - start,
                           "directory": str(destination.resolve())}}
    (args.output / "result.json").write_bytes(canonical_json(result))
    print(canonical_json(result).decode("ascii"))


if __name__ == "__main__":
    main()
