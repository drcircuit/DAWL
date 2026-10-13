"""DAWL platform commands: actual ingestion, vector recovery, ledger replication and audit."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from dawl_node.common import canonical_json, read_json, sha256, write_json
from dawl_platform.artifacts import PublicPeer, sync
from dawl_platform.workflow import prepare, tokenize, reconstruct, assemble, open_node


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "tokenize", "vectors", "audit", "verify", "sync", "assemble", "verify-ledger", "init-replica", "serve", "status"))
    parser.add_argument("--root", type=Path, default=Path("artifacts/platform-v1"))
    parser.add_argument("--raw", type=Path, default=Path("artifacts/book-corpus/raw"))
    parser.add_argument("--lock", type=Path, default=Path("research/corpora/classics-8-v1.lock.json"))
    parser.add_argument("--assets", type=Path, default=Path("artifacts/ingestion-assets"))
    parser.add_argument("--embedding-assets", type=Path, default=Path("artifacts/platform-assets"))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--provider", choices=("cpu", "directml"), default="cpu")
    parser.add_argument("--peer", default="http://127.0.0.1:8880")
    parser.add_argument("--allow-insecure-lab", action="store_true")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--trust-sha256")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8880)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root, args.raw, args.lock, args.assets, args.embedding_assets)
    elif args.command == "init-replica":
        if not args.trust or not args.trust_sha256:
            parser.error("init-replica needs --trust and its independently pinned --trust-sha256")
        trust = read_json(args.trust)
        if sha256(canonical_json(trust)) != args.trust_sha256:
            raise ValueError("trust file differs from independent pin")
        args.root.mkdir(parents=True, exist_ok=True)
        write_json(args.root / "trust.json", trust)
        result = open_node(args.root)[0].checkpoint()
    elif args.command == "tokenize":
        result = tokenize(args.root, args.workers)
    elif args.command == "vectors":
        result = reconstruct(args.root, args.provider)
    elif args.command == "audit":
        from dawl_platform.auditor import audit
        result = audit(args.root, args.peer, args.allow_insecure_lab, args.key)
        print(canonical_json(result).decode())
        return 0 if result["accepted"] else 1
    elif args.command == "sync":
        ledger, objects = open_node(args.root)
        peer = PublicPeer(args.peer, args.allow_insecure_lab)
        result = sync(ledger, peer)
        fetched = 0
        for entry in ledger.entries():
            for descriptor in entry["body"]["payload"].get("files", {}).values():
                objects.fetch(peer, descriptor)
                fetched += 1
        result["artifact_references_checked"] = fetched
    elif args.command == "verify":
        from dawl_platform.auditor import verify_public
        result = verify_public(args.root, args.peer, args.allow_insecure_lab)
        print(canonical_json(result).decode())
        return 0 if result["accepted"] else 1
    elif args.command == "assemble":
        result = assemble(args.root)
    elif args.command == "verify-ledger":
        result = open_node(args.root)[0].verify_history()
    elif args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost", "::1") and not args.allow_insecure_lab:
            parser.error("non-loopback bind needs --allow-insecure-lab")
        import uvicorn
        from dawl_platform.service import app
        uvicorn.run(app(args.root), host=args.host, port=args.port, access_log=False, limit_concurrency=16)
        return 0
    else:
        from dawl_platform.service import summary
        result = summary(args.root)
    print(canonical_json(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
