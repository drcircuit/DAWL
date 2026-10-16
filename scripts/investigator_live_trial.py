"""Assess the three unchanged source-network trial answers through a discovered peer."""
import argparse
import json
from pathlib import Path
import secrets

import httpx

from dawl_evidence.assessment_service import discover, request_assessment
from dawl_evidence.investigator import verify_assessment
from dawl_lab.mesh import configure_policy
from dawl_node.common import canonical_json, load_key, sha256, write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('/var/lib/dawl/investigator-001'))
    p.add_argument('--evidence', type=Path, default=Path('/var/lib/dawl/evidence-001'))
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    evaluation = json.loads((a.root/'evaluation.json').read_bytes())
    previous = json.loads((a.evidence/'trial-v2/trial.json').read_bytes())
    policy = json.loads((a.evidence/'network.json').read_bytes())
    network = sha256(canonical_json(policy)); configure_policy(policy, network)
    key = load_key(a.evidence/'consumer-peer/key.private.pem')
    public = (a.root/'peer/key.public.hex').read_text().strip()
    result = {'schema': 'hypertrix.investigator-live-trial/1', 'investigator': public,
              'consumer': previous['consumer'], 'evaluator_sha256': evaluation['evaluator_sha256'],
              'blueprint': previous['blueprint'], 'samples': []}
    with httpx.Client(timeout=180, trust_env=False, follow_redirects=False) as client:
        peers = discover(client, 'http://127.80.0.16:8913', network, previous['blueprint'],
                         {public}, evaluation['evaluator_sha256'])
        if len(peers) != 1:
            raise ValueError('Expected one admitted Investigator')
        result['discovered_investigators'] = len(peers)
        for name in ('seed-continuation', 'positive-retrieval-control', 'false-retrieval-control'):
            answer = json.loads((a.evidence/'trial-v2'/(name+'-answer.json')).read_bytes())
            collection = json.loads((a.evidence/'trial-v2'/(name+'-receipt.json')).read_bytes())
            nonce = secrets.token_hex(32)
            receipt = request_assessment(client, peers[0], collection, answer['text'], evaluation['evaluator_sha256'], key, nonce)
            checked = verify_assessment(receipt, public, collection, evaluation['evaluator_sha256'], nonce)
            write_json(a.output/(name+'-investigation.json'), receipt)
            body = receipt['body']
            result['samples'].append({'name': name, 'verification': checked, 'nonce': nonce,
                'verdicts': [c['verdict'] for c in body['claims']], 'elapsed_seconds': body['elapsed_seconds'],
                'passage_results': [j['verdict'] for c in body['claims'] for j in c['judgments']],
                'unchanged_answer_sha256': body['answer_sha256']})
    write_json(a.output/'trial.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
