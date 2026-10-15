"""Frozen whole-source holdouts and a training-only byte BPE tokenizer."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import numpy as np
from tokenizers import Tokenizer, models, pre_tokenizers, decoders, trainers
from dawl_finetune.data import clean_bounds
from dawl_node.common import canonical_json, immutable_write, sha256, write_json


def file_hash(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def assign_groups(groups):
    """No file-level split: an entire book or upstream repository family stays together."""
    order = sorted(set(groups), key=lambda g: sha256(('scratch-split-v1/'+g).encode()))
    if len(order) < 3: raise ValueError('At least three source groups are required')
    n = max(1, len(order)//10)
    return {group: 'test' if i < n else 'validation' if i < 2*n else 'train' for i,group in enumerate(order)}


def source_documents(books, code):
    source = json.loads((books/'source-lock.json').read_bytes())
    for book in source['books']:
        path = books/book['file']
        if file_hash(path) != book['sha256']: raise ValueError('Book hash mismatch')
        a,b = clean_bounds(path.read_bytes())
        yield {'id': 'gutenberg:'+str(book['id']), 'group': 'gutenberg:'+str(book['id']),
               'domain': book['domain'], 'source_sha256': book['sha256'], 'start': a, 'end': b,
               'path': str(path), 'origin': book, 'kind': 'book'}
    if not (code/'corpus-lock.json').exists(): raise ValueError('Complete the repository inventory before freezing a Blueprint')
    for path in sorted((code/'repositories').glob('*/snapshot.json')):
        repo = json.loads(path.read_bytes())
        if repo['status'] != 'candidate': continue
        snapshot_digest = file_hash(path)
        for item in repo['files']:
            yield {'id': repo['full_name']+'/'+item['path'], 'group': 'github:'+repo['family'],
                   'domain': 'code', 'source_sha256': item['sha256'], 'start': 0, 'end': item['bytes'],
                   'path': str(code/'objects'/item['sha256']), 'kind': 'code',
                   'origin': {'repository': repo['full_name'], 'commit': repo['commit'], 'file': item['path'],
                              'source_url': item['source_url'], 'license_metadata': repo['license'],
                              'rights_basis': repo['rights_basis'], 'snapshot_sha256': snapshot_digest}}


def prepare(root, books, code, vocab_size=4096, context=512):
    if (root/'blueprint.json').exists(): raise ValueError('Blueprint already exists; preserve it and use another root')
    root.mkdir(parents=True, exist_ok=True)
    documents = list(source_documents(books, code))
    splits = {}
    for domain in sorted({d['domain'] for d in documents}):
        splits.update(assign_groups(d['group'] for d in documents if d['domain'] == domain))
    # Holdouts own duplicate bytes. Training cannot retain a duplicate of a held-out file.
    priority = {'test': 0, 'validation': 1, 'train': 2}
    documents.sort(key=lambda d: (priority[splits[d['group']]], d['id']))
    admitted, exclusions, seen = [], [], set()
    for doc in documents:
        path = Path(doc.pop('path'))
        raw = path.read_bytes()
        if sha256(raw) != doc['source_sha256']: raise ValueError('Source object hash mismatch')
        payload = raw[doc['start']:doc['end']]
        payload.decode('utf-8')
        digest = sha256(payload)
        doc.update(split=splits[doc['group']], text_sha256=digest)
        if digest in seen:
            exclusions.append({'id': doc['id'], 'reason': 'exact-content-duplicate', 'sha256': digest}); continue
        seen.add(digest)
        immutable_write(root/'texts'/digest, payload)
        admitted.append(doc)
    admitted.sort(key=lambda d: d['id'])
    write_json(root/'sources.json', {'documents': admitted, 'exclusions': exclusions,
        'books_lock_sha256': file_hash(books/'source-lock.json'), 'code_lock_sha256': file_hash(code/'corpus-lock.json')})
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=vocab_size, min_frequency=2, show_progress=False,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=['<|bos|>', '<|eos|>', '<|pad|>'])
    def training_texts():
        for doc in admitted:
            if doc['split'] == 'train':
                # Bound long documents for the tokenizer fit; language-model training uses all admitted text.
                yield (root/'texts'/doc['text_sha256']).read_bytes().decode('utf-8')[:32768]
    tokenizer.train_from_iterator(training_texts(), trainer=trainer)
    tokenizer.save(str(root/'tokenizer.json'))
    arrays = defaultdict(list)
    index = []
    eos, pad = tokenizer.token_to_id('<|eos|>'), tokenizer.token_to_id('<|pad|>')
    for doc in admitted:
        text = (root/'texts'/doc['text_sha256']).read_bytes().decode('utf-8')
        ids = tokenizer.encode(text, add_special_tokens=False).ids + [eos]
        start_row = len(arrays[doc['split']])
        targets = 0
        for start in range(0, len(ids)-1, context):
            chunk = ids[start:start+context+1]
            if len(chunk) < 2: continue
            targets += len(chunk)-1
            chunk += [pad]*(context+1-len(chunk))
            arrays[doc['split']].append(np.asarray(chunk, dtype='<u2'))
        index.append({'id': doc['id'], 'split': doc['split'], 'domain': doc['domain'],
                      'start_row': start_row, 'end_row': len(arrays[doc['split']]), 'target_tokens': targets})
    descriptors = {}
    for split in ('train','validation','test'):
        values = np.stack(arrays[split])
        np.save(root/(split+'.npy'), values, allow_pickle=False)
        descriptors[split] = {'sha256': file_hash(root/(split+'.npy')), 'windows': len(values),
                             'targets': sum(i['target_tokens'] for i in index if i['split'] == split)}
    write_json(root/'windows.json', index)
    blueprint = {'schema': 'hypertrix.scratch-blueprint/1', 'parent': None,
        'sources_sha256': file_hash(root/'sources.json'), 'tokenizer_sha256': file_hash(root/'tokenizer.json'),
        'windows_sha256': file_hash(root/'windows.json'), 'arrays': descriptors,
        'context': context, 'vocab_size': tokenizer.get_vocab_size(), 'pad_id': pad, 'eos_id': eos,
        'documents': len(admitted), 'domains': dict(Counter(d['domain'] for d in admitted)),
        'split_policy': 'Whole books by domain and whole upstream repository families; exact duplicates retained once, holdout priority.',
        'tokenizer_fit': 'Train split only, first 32768 characters of each admitted document; byte alphabet included.',
        'limitations': 'Near-duplicate and related-project leakage are not exhaustively excluded. Local source admission, not Oracle certification.'}
    write_json(root/'blueprint.json', blueprint)
    return blueprint


def load(root):
    blueprint = json.loads((root/'blueprint.json').read_bytes())
    for name, key in [('sources.json','sources_sha256'),('tokenizer.json','tokenizer_sha256'),('windows.json','windows_sha256')]:
        if file_hash(root/name) != blueprint[key]: raise ValueError('Blueprint hash mismatch: '+name)
    arrays = {}
    for split, desc in blueprint['arrays'].items():
        if file_hash(root/(split+'.npy')) != desc['sha256']: raise ValueError('Token array hash mismatch')
    for split in blueprint['arrays']:
        arrays[split] = np.load(root/(split+'.npy'), mmap_mode='r', allow_pickle=False)
    return blueprint, arrays
