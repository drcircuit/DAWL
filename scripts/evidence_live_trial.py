"""Explicit local trial: real Seed III continuation plus labeled retrieval controls."""
import argparse
import json
from pathlib import Path

import httpx

from dawl_evidence.consumer import collect, discover
from dawl_evidence.index import Blueprint, file_hash
from dawl_lab.mesh import configure_policy
from dawl_node.common import canonical_json, load_key, sha256, write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=Path('/var/lib/dawl/evidence-001'))
    p.add_argument('--dataset', type=Path, default=Path('/var/lib/dawl/seed-lab-001/dataset'))
    p.add_argument('--output', type=Path, required=True, help='Fresh directory for this trial')
    p.add_argument('--generation', type=Path, help='Reuse an unchanged saved generation for comparisons')
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    pin = file_hash(a.dataset / 'blueprint.json')
    bp = Blueprint(a.dataset, pin)
    policy = json.loads((a.root / 'network.json').read_bytes())
    network = sha256(canonical_json(policy))
    configure_policy(policy, network)
    public = (a.root / 'provider-peer/key.public.hex').read_text().strip()
    key = load_key(a.root / 'consumer-peer/key.private.pem')
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        peers = discover(client, 'http://127.80.0.14:8913', network, pin, {public})
        if not peers:
            raise RuntimeError('Evidence peer has not been discovered')
        request = {'prompt': 'Photosynthesis is the process by which', 'max_tokens': 64, 'temperature': 0, 'seed': 42}
        if a.generation:
            saved = json.loads(a.generation.read_bytes())
            request, generation = saved['request'], saved['response']
        else:
            response = client.post('http://127.0.0.1:8887/api/complete', json=request, timeout=120)
            response.raise_for_status()
            generation = response.json()
        write_json(a.output / 'generation.json', {'request': request, 'response': generation})
        samples = [
            ('seed-continuation', generation['text'], 'model-host-response'),
            ('positive-retrieval-control', 'Photosynthesis converts light energy into chemical energy.', 'constructed-control'),
            ('false-retrieval-control', 'Photosynthesis converts moonlight into gold.', 'constructed-false-control'),
        ]
        summaries = []
        for name, answer, origin in samples:
            if not answer:
                raise RuntimeError('Empty model completion')
            spans = [{'start': 0, 'end': min(len(answer), 2000)}]
            write_json(a.output / (name + '-answer.json'), {'text': answer, 'claims': spans,
                                                        'checkpoint': generation['checkpoint'], 'origin': origin})
            receipt = collect(answer, spans, generation['checkpoint'], bp, peers, key, client)
            write_json(a.output / (name + '-receipt.json'), receipt)
            body = receipt['body']
            summaries.append({'sample': name, 'origin': origin, 'receipt': receipt['id'],
                              'candidates': len(body['claims'][0]['candidates']), 'failures': len(body['failures']),
                              'elapsed_seconds': body['elapsed_seconds'], 'metrics': body['metrics'],
                              'assessment': body['claims'][0]['assessment']})
        result = {'schema': 'hypertrix.evidence-trial/1', 'blueprint': pin,
                  'checkpoint': generation['checkpoint'], 'checkpoint_step': generation['step'],
                  'provider': public, 'consumer': key.public_key().public_bytes_raw().hex(),
                  'discovered_providers': len(peers), 'samples': summaries,
                  'scope': 'Live local HTTP and two-peer discovery; retrieval and integrity, not semantic accuracy.'}
        write_json(a.output / 'trial.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
