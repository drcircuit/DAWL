"""Fetch a frozen small book collection or prepare its separate ingestion fixture."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_corpus.corpus import fetch, prepare
from dawl_node.common import read_json

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "prepare"))
    parser.add_argument("--selection", type=Path, default=Path("research/corpora/classics-8-v1.json"))
    parser.add_argument("--lock", type=Path, default=Path("research/corpora/classics-8-v1.lock.json"))
    parser.add_argument("--raw", type=Path, default=Path("artifacts/book-corpus/raw"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/books-ingestion-v1"))
    parser.add_argument("--assets", type=Path, default=Path("artifacts/ingestion-assets"))
    parser.add_argument("--wsl-distro", default="kali-linux")
    args = parser.parse_args()
    if args.command == "fetch":
        lock = fetch(args.selection, args.raw, args.lock, args.wsl_distro)
        print(f"Locked {len(lock['books'])} books / {lock['source_bytes']:,} original UTF-8 bytes")
    else:
        assets = read_json(args.assets / "assets.lock.json")
        run = prepare(args.lock, args.raw, args.assets / "tokenizer.json", assets["tokenizer.json"]["sha256"], args.output)
        print(f"Prepared {run['book_count']} books in {run['source_documents']} bounded parts / {run['chunks']:,} chunks / {run['tokens']:,} tokens")
        print(f"Snapshot: {run['snapshot_id']}")
