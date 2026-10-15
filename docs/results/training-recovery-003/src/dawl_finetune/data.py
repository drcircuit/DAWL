"""Hash-locked book derivatives and document-segment holdouts."""
import hashlib
import json
import re
from pathlib import Path
import numpy as np
from dawl_node.common import read_json, write_json, sha256


def file_hash(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def clean_bounds(raw):
    """Return byte offsets into the preserved original, excluding PG wrapper."""
    start = re.search(rb'\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^\r\n]*', raw, re.I)
    end = re.search(rb'\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^\r\n]*', raw, re.I)
    if not start or not end or start.end() >= end.start():
        raise ValueError('Expected Gutenberg start/end markers')
    return start.end(), end.start()


def segments(raw, start, end, target=24000):
    cursor = start
    while cursor < end:
        stop = min(end, cursor + target)
        if stop < end:
            boundary = raw.find(b'\n', stop, min(end, stop + 2048))
            stop = boundary + 1 if boundary >= 0 else stop
            while stop < end and raw[stop] & 0xc0 == 0x80:
                stop += 1
        yield cursor, stop
        cursor = stop


def assignments(book_id, count):
    if count < 3:
        raise ValueError('At least three segments required')
    order = sorted(range(count), key=lambda i: sha256(f'holdout-v1/{book_id}/{i}'.encode()))
    n = max(1, count // 10)
    result = ['train'] * count
    for i in order[:n]: result[i] = 'validation'
    for i in order[n:2*n]: result[i] = 'test'
    return result


def prepare(root, tokenizer):
    if (root/'dataset-lock.json').exists():
        raise ValueError('Dataset already prepared')
    source = read_json(root/'source-lock.json')
    arrays = {f'{split}_{domain}': [] for split in ('train','validation','test') for domain in ('economics','literature')}
    rows, seen, duplicates = [], set(), 0
    for book in source['books']:
        raw = (root/book['file']).read_bytes()
        if sha256(raw) != book['sha256']: raise ValueError('Source bytes changed')
        start, end = clean_bounds(raw)
        ranges = list(segments(raw, start, end))
        splits = assignments(book['id'], len(ranges))
        for index, ((a,b), split) in enumerate(zip(ranges,splits)):
            tokens = tokenizer.encode(raw[a:b].decode('utf-8'), add_special_tokens=False)
            # Drop context at EVERY segment boundary, before constructing windows.
            tokens = tokens[128:-128]
            key = f'{split}_{book["domain"]}'
            count = 0
            for offset in range(0, len(tokens)-512, 513):
                window = np.asarray(tokens[offset:offset+513],dtype='<u4')
                digest = sha256(window.tobytes())
                if digest in seen:
                    duplicates += 1
                    continue
                seen.add(digest); arrays[key].append(window); count += 1
            rows.append({'book_id':book['id'],'domain':book['domain'],'segment':index,'split':split,
                         'start_byte':a,'end_byte':b,'segment_sha256':sha256(raw[a:b]),'windows':count})
    packed = {key:np.asarray(value,dtype='<u4').reshape(-1,513) for key,value in arrays.items()}
    if any(len(a)==0 for a in packed.values()): raise ValueError('Empty split')
    np.savez(root/'dataset.npz',**packed)
    with (root/'segments.jsonl').open('x') as f:
        for row in rows: f.write(json.dumps(row,sort_keys=True)+'\n')
    lock = {'schema':'hypertrix.finetune-dataset/1','source_lock_sha256':file_hash(root/'source-lock.json'),
            'base_lock_sha256':file_hash(root/'base-lock.json'),'dataset_sha256':file_hash(root/'dataset.npz'),
            'segments_sha256':file_hash(root/'segments.jsonl'),'sequence_length':512,'boundary_guard_tokens':128,
            'split_rule':'Per-book SHA256-ranked 24KB contiguous segments; 10% validation, 10% test, remainder train; floor with minimum one.',
            'eval_rule':'First 64 windows of each domain/split in frozen source order; test only baseline and selected checkpoint.',
            'limitations':'No exact duplicate 513-token windows across splits; near-duplicate prose and baseline pretraining overlap remain possible.',
            'dropped_duplicate_windows':duplicates,'windows':{k:len(v) for k,v in packed.items()},
            'prediction_tokens':{k:len(v)*512 for k,v in packed.items()}}
    write_json(root/'dataset-lock.json',lock)
    return lock


def load(root):
    lock = read_json(root/'dataset-lock.json')
    for filename,field in [('dataset.npz','dataset_sha256'),('segments.jsonl','segments_sha256'),
                           ('source-lock.json','source_lock_sha256'),('base-lock.json','base_lock_sha256')]:
        if file_hash(root/filename) != lock[field]: raise ValueError('Dataset provenance mismatch: '+filename)
    with np.load(root/'dataset.npz',allow_pickle=False) as values:
        return {k:values[k].astype(np.int64) for k in values.files}
