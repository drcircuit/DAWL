"""Check a Seed artifact directory against a separately obtained report's public anchors.

This checks bytes and signed claims. It does not rerun optimization; replay receipts
describe that work and its exact interval. No private key or GPU is needed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dawl_node.common import verify_signature


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(root, report):
    dataset = root / 'dataset'
    blueprint_hash = digest(dataset / 'blueprint.json')
    require(blueprint_hash == report['blueprint_sha256'], 'Blueprint anchor differs')
    blueprint = json.loads((dataset / 'blueprint.json').read_bytes())
    for filename, field in [('sources.json', 'sources_sha256'), ('windows.json', 'windows_sha256'),
                            ('tokenizer.json', 'tokenizer_sha256')]:
        require(digest(dataset / filename) == blueprint[field], f'{filename} differs')
    for split, desc in blueprint['arrays'].items():
        require(digest(dataset / (split + '.npy')) == desc['sha256'], f'{split} tokens differ')
    data_receipt = json.loads((root / 'data-verifier/receipt.json').read_bytes())
    data_key = report['data_verification']['body']['signer']
    data_body = verify_signature(data_receipt, data_key)
    require(data_receipt == report['data_verification'], 'Data receipt differs from report')
    require(data_body['accepted'] and data_body['blueprint_sha256'] == blueprint_hash,
            'Data receipt does not accept this Blueprint')
    require(digest(root / 'data-verifier/implementation.py') == data_body['verification_code_sha256'],
            'Data verifier implementation differs')
    result = {'blueprint_sha256': blueprint_hash, 'data_receipt_signature': True, 'models': {}}
    known_checkpoints = {}
    for name in ('dense', 'moe'):
        if name not in report['models']:
            continue
        model = root / name
        entry = report['models'][name]
        public = entry['operator_public_key']
        require(digest(model / 'lineage.jsonl') == entry['lineage_sha256'], f'{name} lineage anchor differs')
        previous = None
        bodies = []
        for index, line in enumerate((model / 'lineage.jsonl').read_text().splitlines()):
            envelope = json.loads(line)
            body = verify_signature(envelope, public)
            require(body['sequence'] == index and body['previous'] == previous, 'Broken record chain')
            bodies.append(body)
            previous = envelope['id']
        require(bodies and bodies[0]['kind'] == 'initialization', 'Missing initialization')
        initial = bodies[0]['payload']
        for filename, field in [('config.json', 'config_sha256'), ('recipe.json', 'recipe_sha256'),
                                ('implementation.json', 'implementation_sha256')]:
            require(digest(model / filename) == initial[field], f'{name} {filename} differs')
        implementation = json.loads((model / 'implementation.json').read_bytes())
        for filename, expected in implementation.items():
            require(Path(filename).name == filename, 'Invalid implementation path')
            require(digest(model / 'implementation' / filename) == expected, 'Frozen implementation differs')
        recipe = json.loads((model / 'recipe.json').read_bytes())
        require(recipe['blueprint_sha256'] == blueprint_hash, 'Recipe uses another Blueprint')
        if initial['parent']:
            parent = initial['parent']
            require((parent['operator'], parent['checkpoint_sha256']) in known_checkpoints,
                    'MoE parent is not a verified dense checkpoint')
        checkpoints = 0
        for body in bodies:
            if body['kind'] != 'checkpoint':
                continue
            payload = body['payload']
            descriptor = model / 'checkpoints' / f'{payload["step"]:07d}' / 'checkpoint.json'
            require(digest(descriptor) == payload['sha256'], 'Checkpoint descriptor differs')
            desc = json.loads(descriptor.read_bytes())
            require(desc['config_sha256'] == initial['config_sha256'] and
                    desc['recipe_sha256'] == initial['recipe_sha256'], 'Checkpoint recipe differs')
            require(digest(descriptor.parent / 'state.safetensors') == desc['state_sha256'],
                    'Checkpoint model/optimizer bytes differ')
            known_checkpoints[(public, payload['sha256'])] = True
            checkpoints += 1
        summary = json.loads((model / 'summary.json').read_bytes())
        require(summary == entry['summary'], 'Selected result differs from report')
        signed_results = [b['payload']['sha256'] for b in bodies if b['kind'] == 'training-result']
        require(digest(model / 'summary.json') in signed_results, 'Result is not signed')
        intervals = []
        for receipt in sorted(model.glob('replay-*.json')):
            payloads = [b['payload'] for b in bodies if b['kind'] == 'replay' and
                        b['payload']['sha256'] == digest(receipt)]
            require(len(payloads) == 1, 'Replay receipt is not uniquely signed')
            value = json.loads(receipt.read_bytes())
            require(all(value[k] == v for k, v in payloads[0].items() if k != 'sha256'),
                    'Replay statement differs from receipt')
            require(value['accepted'] and value['optimizer_exact'] and
                    value['exact_tensors'] == value['tensor_count'], 'Replay did not pass')
            intervals.append([value['start_step'], value['end_step']])
        result['models'][name] = {'signed_records': len(bodies), 'checkpoint_files_verified': checkpoints,
                                 'selected_step': summary['selected_step'], 'replay_intervals': intervals}
    result['accepted'] = True
    result['scope'] = 'Artifact integrity against report anchors; signatures certify recorded claims, replay covers listed intervals.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True,
                        help='Public metadata report obtained through a trusted channel or pinned repository commit')
    args = parser.parse_args()
    print(json.dumps(verify(args.root, json.loads(args.report.read_bytes())), indent=2))
