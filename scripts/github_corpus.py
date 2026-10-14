"""Pin and fetch all public repositories for a corpus, without executing their code."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dawl_corpus.github import fetch_all

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', default='drcircuit', choices=['drcircuit'])
    parser.add_argument('--root', type=Path, default=Path.home()/'.cache/hypertrix/github-drcircuit-001')
    args = parser.parse_args()
    result = fetch_all(args.owner, args.root)
    print(json.dumps({'root': str(args.root), 'statuses': result['statuses']}))
    sys.exit(1 if result['statuses'].get('fetch-error') else 0)
