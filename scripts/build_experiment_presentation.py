"""Build a public, allowlisted presentation bundle from archived experiment evidence."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
OUT = ROOT / 'output/testnet-presentation'


def read(run, filename='report.json'):
    return json.loads((DOCS / 'results' / run / filename).read_text(encoding='utf-8'))


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'evidence').mkdir(exist_ok=True)
    registry = {}
    def evidence(path, label):
        source = DOCS / path
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        name = digest + source.suffix
        shutil.copyfile(source, OUT / 'evidence' / name)
        item = {'id': digest, 'file': name, 'label': label, 'source': path,
                'sha256': digest, 'bytes': len(raw), 'format': source.suffix[1:]}
        registry[digest] = item
        return item

    def run(key, title, category, result, boundary, files):
        return {'id': key, 'title': title, 'category': category, 'result': result,
                'boundary': boundary,
                'evidence': [evidence(path, label) for path, label in files]}

    catalog = [
        run('platform-run-001', '01 · Source to Synapses', 'Ingestion',
            'Eight books, 1,438 Frags and 36 signed lineage records; full independent CPU replay passed.',
            'Local federation. Input embeddings only; imported pretraining is outside the provenance boundary.',
            [('results/platform-run-001/summary.json', 'Measurements'), ('results/platform-run-001/public-verification.json', 'Public verification'), ('platform_run_001.md', 'Method and findings')]),
        run('swarm-run-001', '02 · An adversarial swarm', 'Resilience',
            'Five containers converged after corruption, withholding and partition; independent replay contradicted a dummy approval.',
            '16 sampled Frags per valid book; simulated network friction on one host.',
            [('results/swarm-run-001/report.json', 'Recorded exercise'), ('results/swarm-run-001/observation-verification.json', 'Observation verification'), ('swarm_run_001.md', 'Method and findings')]),
        run('cluster-run-002', '03 · Consensus under failure', 'Resilience',
            'Three validators continued; two halted; restoring a third resumed commits. Expired lease results were rejected.',
            'One physical host; admitted workers. No training in this run.',
            [('results/cluster-run-002/report.json', 'Fault phases'), ('cluster_run_002.md', 'Method and findings')]),
        run('external-run-001', '04 · Hostile transport rehearsal', 'Resilience',
            'Full-corpus release survived an isolated validator, forged RPC state, unavailable provider and corrupt delayed pieces.',
            'Local isolated containers, not an external multi-operator deployment.',
            [('results/external-run-001/report.json', 'Recorded exercise'), ('external_run_001.md', 'Method and findings')]),
        run('hybrid-gpu-run-001', '05 · Hybrid vector reconstruction', 'Vectors',
            '6.332 GB of F32 input vectors reconstructed from 172.5 MB received, with 1,807 CUDA launches in 66.58 seconds.',
            'Full end-to-end observation, not a disk-speed comparison. Signed GPU diagnostics were not Oracle vector consensus.',
            [('results/hybrid-gpu-run-001/summary.json', 'Measurements'), ('results/hybrid-gpu-run-001/last-report.json', 'Signed GPU receipt'), ('gpu_run_001.md', 'Method and findings')]),
        run('multiprofile-run-001', '06 · Multiple tokenizer lineages', 'Profiles',
            'Five local Blueprints replayed the same 13 books; cross-profile substitution was rejected. Raw objects were shared.',
            'Tokenization and lineage only; these initial local Blueprints were not Oracle certificates.',
            [('results/multiprofile-run-001/report.json', 'Profile evidence'), ('results/multiprofile-run-001/README.md', 'Method and findings')]),
        run('o200k-run-001', '07 · o200k ordinary tokens', 'Profiles',
            '1,753,309 tokens from the same corpus, 14.94% fewer than GPT-2 under the pinned Frag contract.',
            'Tokenizer-only profile; fewer tokens is not evidence of higher model quality.',
            [('results/o200k-run-001/report.json', 'Profile evidence'), ('results/o200k-run-001/README.md', 'Method and findings')]),
        run('profile-testnet-001', '08 · The interrupted profile probe', 'Engineering',
            'An interrupted probe exposed excessive transfer-telemetry disk commits. The later run used bounded batching.',
            'Preserved engineering finding, not counted as a completed six-profile run.',
            [('results/profile-testnet-001/README.md', 'Probe findings')]),
        run('profile-testnet-002', '09 · Six certified Blueprints', 'Profiles',
            '78 book/profile jobs, 156 signed audit receipts, six independent heads; validator and auditor recovery exercised.',
            'Token provenance and full retokenization. One host and one operator.',
            [('results/profile-testnet-002/report.json', 'Measurements'), ('results/profile-testnet-002/verification.json', 'Verification'), ('results/profile-testnet-002/README.md', 'Method and findings')]),
        run('profile-gpu-run-001', '10 · Five model-bound GPU profiles', 'Vectors',
            '9,035 profile/Frag executions reconstructed 39.732 GB of vectors from 3.119 GB of cold HTTP payload.',
            'Bit-exact input row lookup. Cold-cache payload excludes upstream checkpoint acquisition and wire framing.',
            [('results/profile-gpu-run-001/report.json', 'Measurements and receipts'), ('results/profile-gpu-run-001/verification.json', 'Signature and binding checks'), ('results/profile-gpu-run-001/README.md', 'Method and equations')]),
        run('o200k-gpu-run-001', '11 · o200k + GPT-OSS input rows', 'Vectors',
            '199,998 ordinary token mappings verified; 20.198 GB of vectors reconstructed across all 1,807 Frags.',
            'Ordinary-token compatibility only. The derived model-bound execution profile was not Oracle-certified.',
            [('results/o200k-gpu-run-001/report.json', 'Measurements and compatibility'), ('results/o200k-gpu-run-001/verification.json', 'Verification'), ('results/o200k-gpu-run-001/README.md', 'Method and equations')]),
        run('training-run-001', '12 · Peer training and sampled audit', 'Training',
            '64 adapter contributions, 32 common checkpoints and two exact post-commit training replays; a surviving peer served the model.',
            'Fixed roster, local signed branch. Only two of 64 contributions numerically replayed; no Oracle training admission.',
            [('results/training-run-001/summary.json', 'Measurements and evaluation'), ('results/training-run-001/verification.json', 'Lineage verification'), ('results/training-run-001/README.md', 'Method and findings')]),
        run('oracle-training-run-003', '13 · Oracle-managed training', 'Training',
            'Eight contributions, 16 exact GPU replays and four certified checkpoints. A new trainer replaced an expired lease owner.',
            'Bounded partial pass; one shared GPU. All named verifiers must agree; invalid accepted updates can still stall a stage.',
            [('results/oracle-training-run-003/summary.json', 'Measurements'), ('results/oracle-training-run-003/verification.json', 'Full state replay'), ('results/oracle-training-run-003/training-certificate.json', 'Quorum certificate'), ('results/oracle-training-run-003/application-history.json', 'Application history'), ('results/oracle-training-run-003/availability.json', 'Cold consumer'), ('results/oracle-training-run-003/churn.json', 'Lease replacement'), ('results/oracle-training-run-003/README.md', 'Method and findings'), ('oracle_training.md', 'Architecture and mathematics')]),
    ]
    vectors = []
    for name in ('profile-gpu-run-001', 'o200k-gpu-run-001'):
        for p in read(name)['profiles']:
            vectors.append({k:p[k] for k in ('name','tokens','frags','dense_bytes','http_payload_bytes','total_seconds','table_bytes')})
    hybrid = read('hybrid-gpu-run-001','summary.json')
    hybrid['dense_bytes'] = read('hybrid-gpu-run-001','last-report.json')['body']['dense_bytes']
    profiles = read('profile-testnet-002')['profiles']
    prior = read('training-run-001','summary.json')
    current = read('oracle-training-run-003','summary.json')
    data = {'schema':'hypertrix.experiment-presentation/1', 'catalog': catalog,
            'vectors':vectors, 'hybrid':hybrid, 'profiles':profiles,
            'training':{'prior':prior,'current':current},
            'verification':read('oracle-training-run-003','verification.json'),
            'provenance':'Figures extracted from archived experiment reports. The page does not rerun their cryptographic or numerical checks.',
            'evidence':registry}
    (OUT/'index.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'experiments':len(catalog),'evidence_files':len(registry),'output':str(OUT)}))


if __name__ == '__main__': build()
