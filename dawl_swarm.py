"""DAWL configured-bootnode swarm and isolated adversarial lab."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from dawl_node.common import canonical_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare-lab", "serve", "status", "verify-receipt"))
    parser.add_argument("--root", type=Path, default=Path("artifacts/swarm-lab-001"))
    parser.add_argument("--source", type=Path, default=Path("artifacts/platform-v1"))
    parser.add_argument("--key", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--allow-insecure-lab", action="store_true")
    parser.add_argument("--peer")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--policy-sha256")
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--trust-sha256")
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if args.command == "prepare-lab":
        from dawl_swarm.lab import prepare_lab
        result = prepare_lab(args.root, args.source)
    elif args.command == "verify-receipt":
        if not all((args.peer, args.policy, args.policy_sha256, args.trust, args.trust_sha256, args.receipt)):
            parser.error("verify-receipt needs --peer, --receipt, --policy, --policy-sha256, --trust and --trust-sha256")
        import asyncio
        from dawl_swarm.public import replay
        result = asyncio.run(replay(args.root, args.peer, args.policy, args.policy_sha256, args.trust, args.trust_sha256, args.receipt))
        print(canonical_json(result).decode())
        return 0 if result["matches_claim"] is True else 1
    elif args.command == "serve":
        from dawl_swarm.service import app
        import uvicorn
        if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_insecure_lab:
            parser.error("nonloopback binding requires --allow-insecure-lab")
        uvicorn.run(app(args.root, args.key), host=args.host, port=args.port, access_log=False, limit_concurrency=32)
        return
    else:
        from dawl_swarm.node import Node
        result = Node(args.root, args.key).status()
    print(canonical_json(result).decode())


if __name__ == "__main__":
    raise SystemExit(main())
