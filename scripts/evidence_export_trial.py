"""Export an explicit public evidence allowlist, never node identity directories."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=Path('/var/lib/dawl/evidence-001'))
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    files = {'index.json': a.root / 'index/index.json', 'initial-trial.json': a.root / 'trial.json'}
    for name in ('trial.json', 'generation.json', 'offline-verification.json',
                 'seed-continuation-answer.json', 'seed-continuation-receipt.json',
                 'positive-retrieval-control-answer.json', 'positive-retrieval-control-receipt.json',
                 'false-retrieval-control-answer.json', 'false-retrieval-control-receipt.json'):
        files[name] = a.root / 'trial-v2' / name
    a.output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, source in files.items():
        raw = source.read_bytes()
        if b'PRIVATE KEY-----' in raw or b'sk-proj-' in raw:
            raise ValueError('Private material found in evidence export')
        target = a.output / name
        if target.exists() and target.read_bytes() != raw:
            raise ValueError('Evidence output already differs')
        target.write_bytes(raw)
        manifest[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    code = {}
    runtime = a.root / 'runtime-v2/src'
    for path in sorted((runtime / 'dawl_evidence').glob('*.py')):
        code[path.relative_to(runtime).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('dawl_lab/mesh.py', 'dawl_node/common.py', 'data_ingestion/protocol.py'):
        code[name] = hashlib.sha256((runtime / name).read_bytes()).hexdigest()
    (a.output / 'evidence.json').write_text(json.dumps({'schema': 'hypertrix.evidence-trial-export/1',
        'files': manifest, 'provider_runtime_sources': code,
        'scope': 'Public trial records; full committed source objects and index remain in the native lab.'},
        ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'files': len(files), 'bytes': sum(v['bytes'] for v in manifest.values())}))


if __name__ == '__main__':
    main()
