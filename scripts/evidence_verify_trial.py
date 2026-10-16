"""Read-only verifier for the live trial; requires no provider or consumer keys."""
import argparse
import json
from pathlib import Path

from dawl_evidence.consumer import verify_collection
from dawl_evidence.index import Blueprint, source_bytes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--trial', type=Path, required=True)
    p.add_argument('--dataset', type=Path, required=True)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--blueprint', required=True)
    p.add_argument('--consumer', required=True)
    a = p.parse_args()
    bp = Blueprint(a.dataset, a.blueprint)
    results = []
    for name in ('seed-continuation', 'positive-retrieval-control', 'false-retrieval-control'):
        answer = json.loads((a.trial / (name + '-answer.json')).read_bytes())['text']
        receipt = json.loads((a.trial / (name + '-receipt.json')).read_bytes())
        result = verify_collection(receipt, a.consumer, bp, answer, lambda digest: source_bytes(a.corpus, digest))
        results.append({'sample': name, **result})
    print(json.dumps({'network': 'disabled', 'private_keys': 'not_mounted', 'results': results}), flush=True)


if __name__ == '__main__':
    main()
