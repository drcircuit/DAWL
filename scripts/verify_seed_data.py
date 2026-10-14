"""Verify the expanded corpus against its frozen parent and original TEI archive."""
import argparse
import json
from pathlib import Path
import tarfile
import numpy as np
from tokenizers import Tokenizer
from dawl_node.common import keygen, load_key, sign, write_json, sha256
from dawl_seed.data import load, file_hash
from dawl_scratch.data import load as parent_load
from dawl_seed.intake import extract
from dawl_seed.progress import progress
from dawl_seed.groups import resolve
p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--parent', type=Path, required=True)
p.add_argument('--intake', type=Path, required=True)
a = p.parse_args()
bp, arrays = load(a.root)
old, old_arrays = parent_load(a.parent)
if not bp['parent'] == file_hash(a.parent / 'blueprint.json'):
    raise ValueError("Data integrity check failed: bp['parent'] == file_hash(a.parent / 'blueprint.json')")
sources = json.loads((a.root / 'sources.json').read_bytes())
old_sources = json.loads((a.parent / 'sources.json').read_bytes())['documents']
if not sources['documents'][:len(old_sources)] == old_sources:
    raise ValueError("Data integrity check failed: sources['documents'][:len(old_sources)] == old_sources")
if not sources['intake_lock_sha256'] == file_hash(a.intake / 'source-lock.json'):
    raise ValueError("Data integrity check failed: sources['intake_lock_sha256'] == file_hash(a.intake / 'source-lock.json')")
if not sources['catalogue_snapshots_sha256'] == file_hash(a.intake / 'downloads.json'):
    raise ValueError("Data integrity check failed: sources['catalogue_snapshots_sha256'] == file_hash(a.intake / 'downloads.json')")
policy = json.loads((a.root / 'group-policy.json').read_bytes())
resolved, expected_policy = resolve(json.loads((a.intake / 'source-lock.json').read_bytes())['sources'])
if not policy == expected_policy:
    raise ValueError('Data integrity check failed: policy == expected_policy')
for item in json.loads((a.intake / 'downloads.json').read_bytes()):
    if not file_hash(a.intake / item['file']) == item['sha256']:
        raise ValueError("Data integrity check failed: file_hash(a.intake / item['file']) == item['sha256']")
old_index = json.loads((a.parent / 'windows.json').read_bytes())
index = json.loads((a.root / 'windows.json').read_bytes())
if not index[:len(old_index)] == old_index:
    raise ValueError('Data integrity check failed: index[:len(old_index)] == old_index')
evaluation = json.loads((a.root / 'evaluation.json').read_bytes())
for split, values in old_arrays.items():
    if not np.array_equal(values, arrays[split][:len(values)]):
        raise ValueError('Data integrity check failed: np.array_equal(values, arrays[split][:len(values)])')
    if split == 'train':
        continue
    for domain in old['domains']:
        ids = [i for row in old_index if row['split'] == split and row['domain'] == domain for i in range(row['start_row'], row['end_row'])]
        expected = np.asarray(ids)[np.linspace(0, len(ids) - 1, min(32, len(ids)), dtype=int)].tolist()
        if not evaluation[split][domain] == expected:
            raise ValueError('Data integrity check failed: evaluation[split][domain] == expected')
groups = {}
hash_splits = {}
identities = {}
for doc in sources['documents']:
    if not file_hash(a.root / 'texts' / doc['text_sha256']) == doc['text_sha256']:
        raise ValueError("Data integrity check failed: file_hash(a.root / 'texts' / doc['text_sha256']) == doc['text_sha256']")
    groups.setdefault(doc['group'], set()).add(doc['split'])
    hash_splits.setdefault(doc['text_sha256'], set()).add(doc['split'])
    if doc['domain'] == 'norwegian':
        if not doc['group'] == resolved[doc['id']]:
            raise ValueError("Data integrity check failed: doc['group'] == resolved[doc['id']]")
        for author in policy['identities'][doc['id']]:
            identities.setdefault(author, set()).add(doc['split'])
if not (all((len(v) == 1 for v in groups.values())) and all((len(v) == 1 for v in hash_splits.values()))):
    raise ValueError('Data integrity check failed: all((len(v) == 1 for v in groups.values())) and all((len(v) == 1 for v in hash_splits.values()))')
if not all((len(v) == 1 for v in identities.values())):
    raise ValueError('Data integrity check failed: all((len(v) == 1 for v in identities.values()))')
tok = Tokenizer.from_file(str(a.root / 'tokenizer.json'))
rows = {r['id']: r for r in index}
count = 0
with tarfile.open(a.intake / 'bokselskap.tar.gz') as tar:
    archive_hash = file_hash(a.intake / 'bokselskap.tar.gz')
    for doc in sources['documents'][len(old_sources):]:
        origin = doc['origin']
        if not archive_hash == origin['archive_sha256']:
            raise ValueError("Data integrity check failed: archive_hash == origin['archive_sha256']")
        raw = tar.extractfile(origin['archive_member']).read()
        if not sha256(raw) == doc['source_sha256']:
            raise ValueError("Data integrity check failed: sha256(raw) == doc['source_sha256']")
        text, meta = extract(raw)
        if not sha256(text) == doc['text_sha256']:
            raise ValueError("Data integrity check failed: sha256(text) == doc['text_sha256']")
        tokens = tok.encode(text.decode('utf-8'), add_special_tokens=False).ids + [bp['eos_id']]
        r = rows[doc['id']]
        expected = []
        for start in range(0, len(tokens) - 1, bp['context']):
            part = tokens[start:start + bp['context'] + 1]
            expected.append(part + [bp['pad_id']] * (bp['context'] + 1 - len(part)))
        if not np.array_equal(np.asarray(expected, dtype='<u2'), arrays[r['split']][r['start_row']:r['end_row']]):
            raise ValueError("Data integrity check failed: np.array_equal(np.asarray(expected, dtype='<u2'), arrays[r['split']][r['start_row']:r['end_row']])")
        if not r['target_tokens'] == len(tokens) - 1:
            raise ValueError("Data integrity check failed: r['target_tokens'] == len(tokens) - 1")
        count += 1
        progress(a.root, 'Verifying corpus', job='Seed II data replay', role='tokenizer', status='running', completed=count, total=bp['domains']['norwegian'], unit='documents')
result = {'schema': 'hypertrix.seed-data-verification/1', 'accepted': True, 'blueprint_sha256': file_hash(a.root / 'blueprint.json'), 'parent_blueprint_sha256': bp['parent'], 'legacy_documents_unchanged': len(old_sources), 'new_documents_reconstructed': count, 'legacy_arrays_and_evaluation_exact': True, 'author_groups_disjoint': True, 'exact_content_splits_disjoint': True, 'canonical_identities_disjoint': True, 'group_policy_sha256': bp['group_policy_sha256'], 'verifier_sha256': file_hash(Path(__file__)), 'extractor_sha256': file_hash(Path(__import__('dawl_seed.intake', fromlist=['x']).__file__)), 'scope': 'Original archive-to-text-to-token-window replay for new documents; exact parent rows and holdouts. Shared pinned TEI extraction implementation; no near-duplicate claim.'}
keygen(a.root / 'verifier.private.pem', a.root / 'verifier.public.key')
receipt = sign(result, load_key(a.root / 'verifier.private.pem'))
write_json(a.root / 'data-verification.json', receipt)
progress(a.root, 'Corpus verified', job='Seed II data replay', role='tokenizer', status='complete', completed=count, total=count, unit='documents')
print(json.dumps(result), flush=True)
