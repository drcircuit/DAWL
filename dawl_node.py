"""Separate source-provider, tokenizer/miner, storage, validator, and consumer hats."""

import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dawl_node.common import immutable_write, keygen, load_key, load_public, read_bytes, write_json
from dawl_node.source import load_registry, register
from dawl_node.tokenizer import PinnedTokenizer
from dawl_node.store import mine, verify_snapshot
from dawl_node.transport import PeerClient, consume
from dawl_node.audit import audit_peer


def token_file(path):
    return read_bytes(path, 1024).decode("ascii").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("keygen", help="Create a distinct identity for one role")
    identity.add_argument("--directory", type=Path, required=True)
    source = commands.add_parser("register", help="Commit source documents before tokenization")
    source.add_argument("--input", type=Path, required=True)
    source.add_argument("--output", type=Path, required=True)
    source.add_argument("--chunk-bytes", type=int, default=4096)

    def trusted(command, public=True):
        command.add_argument("--registry", type=Path, required=True)
        command.add_argument("--registry-id", required=True)
        command.add_argument("--tokenizer", type=Path, required=True)
        command.add_argument("--tokenizer-sha256", required=True)
        if public:
            command.add_argument("--miner-public-key", type=Path, required=True)

    def peer_options(command):
        command.add_argument("--peer", required=True)
        command.add_argument("--peer-token-file", type=Path, required=True)
        command.add_argument("--allow-insecure-lab", action="store_true")

    miner = commands.add_parser("mine", help="Tokenize and sign a registered source")
    trusted(miner, public=False)
    miner.add_argument("--input", type=Path, required=True)
    miner.add_argument("--store", type=Path, required=True)
    miner.add_argument("--key", type=Path, required=True)
    miner.add_argument("--shard-tokens", type=int, default=131072)

    for name in ("verify", "consume", "audit"):
        command = commands.add_parser(name)
        trusted(command)
        command.add_argument("--snapshot", required=True)
        if name != "verify":
            peer_options(command)
        if name != "audit":
            command.add_argument("--store", type=Path, required=True)
        else:
            command.add_argument("--samples", type=int, default=8)
            command.add_argument("--key", type=Path, required=True)
            command.add_argument("--output", type=Path)

    for name, port in (("serve-storage", 8765), ("serve-validator", 8766)):
        command = commands.add_parser(name)
        command.add_argument("--host", default="127.0.0.1")
        command.add_argument("--port", type=int, default=port)
        command.add_argument("--token-file", type=Path, required=True)
        if name == "serve-storage":
            command.add_argument("--store", type=Path, required=True)
        else:
            trusted(command)
            peer_options(command)
            command.add_argument("--key", type=Path, required=True)
            command.add_argument("--receipts", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            public = keygen(args.directory / "key.private.pem", args.directory / "public.key")
            immutable_write(args.directory / "api.token", (secrets.token_urlsafe(32) + "\n").encode("ascii"))
            result = {"public_key": public, "directory": str(args.directory), "api_token": "saved to api.token"}
        elif args.command == "register":
            result = register(args.input, args.output, args.chunk_bytes)
        elif args.command == "serve-storage":
            from dawl_node.service import storage_app
            import uvicorn
            uvicorn.run(storage_app(args.store, token_file(args.token_file)), host=args.host, port=args.port,
                        limit_concurrency=16, timeout_keep_alive=5, access_log=False)
            return 0
        else:
            registry = load_registry(args.registry, args.registry_id)
            tokenizer = PinnedTokenizer(args.tokenizer, args.tokenizer_sha256)
            if args.command == "mine":
                result = mine(args.input, args.registry, tokenizer, args.key, args.store, args.shard_tokens)
            else:
                public = load_public(args.miner_public_key)
                if args.command == "verify":
                    result = verify_snapshot(args.store / "snapshots" / args.snapshot, registry, tokenizer, public)
                else:
                    peer = PeerClient(args.peer, token_file(args.peer_token_file), allow_insecure=args.allow_insecure_lab)
                    if args.command == "consume":
                        result = consume(peer, args.snapshot, registry, tokenizer, public, args.store)
                    elif args.command == "audit":
                        result = audit_peer(peer, args.snapshot, registry, tokenizer, public, args.samples, load_key(args.key))
                        if args.output:
                            write_json(args.output, result)
                    else:
                        from dawl_node.service import validator_app
                        import uvicorn
                        app = validator_app(peer, registry, tokenizer, public, load_key(args.key), token_file(args.token_file), args.receipts)
                        uvicorn.run(app, host=args.host, port=args.port, limit_concurrency=8,
                                    timeout_keep_alive=5, access_log=False)
                        return 0
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 1 if args.command == "audit" and not result["body"]["accepted"] else 0
    except (ValueError, OSError) as error:
        parser.exit(2, f"dawl-node: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
