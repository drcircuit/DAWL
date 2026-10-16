"""Rebuildable CPU search index; source identity comes from the pinned Blueprint."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from data_ingestion.protocol import require_digest
from dawl_node.common import parse_json, sha256, write_json

METHOD = 'sqlite-fts5-bm25-unicode61/1'
QUERY_METHOD = 'sqlite-fts5-bm25-stop-en-no/2'
# A bounded lexical baseline, not linguistic/semantic claim analysis.
STOP_WORDS = frozenset(('a an and are as at be been being but by can could did do does for from '
                       'had has have he her here him his how i if in into is it its may might more most '
                       'not of on or our she should so some than that the their them then there these '
                       'they this those through to too used using use was we were what when where which '
                       'who will with would you your en ei et og er av at det den de dem som til fra '
                       'på i for med om ikke har hadde ha var være blir ble kan kunne skal skulle '
                       'jeg du han hun vi dere seg sin sitt sine denne dette disse').split())
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024 * 1024


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Blueprint:
    def __init__(self, dataset, expected):
        require_digest(expected)
        self.identity = expected
        dataset = Path(dataset)
        raw = (dataset / 'blueprint.json').read_bytes()
        if sha256(raw) != expected:
            raise ValueError('Blueprint pin differs')
        self.body = parse_json(raw)
        if self.body.get('schema') != 'hypertrix.lab-blueprint/1':
            raise ValueError('Unsupported Blueprint schema')
        source_path = dataset / 'sources.json'
        if source_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError('Source manifest exceeds limit')
        source_bytes = source_path.read_bytes()
        if sha256(source_bytes) != self.body['sources_sha256']:
            raise ValueError('Source manifest differs from Blueprint')
        rows = parse_json(source_bytes, limit=MAX_MANIFEST_BYTES)['documents']
        self.documents = {}
        for row in rows:
            require_digest(row['text_sha256'])
            if row['id'] in self.documents:
                raise ValueError('Duplicate source identity')
            self.documents[row['id']] = row


def source_bytes(corpus, digest):
    require_digest(digest)
    path = Path(corpus) / 'texts' / digest
    if path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('Invalid source object')
    raw = path.read_bytes()
    if sha256(raw) != digest:
        raise ValueError('Source object hash differs')
    raw.decode('utf-8')
    return raw


def passages(text, width=1200):
    """Unicode codepoint offsets into the exact normalized source; no rewriting."""
    start = 0
    while start < len(text):
        end = min(start + width, len(text))
        if end < len(text):
            boundary = max(text.rfind('\n', start + width // 2, end),
                           text.rfind(' ', start + width // 2, end))
            if boundary > start:
                end = boundary + 1
        yield start, end, text[start:end]
        start = end


def build(root, dataset, corpus, expected, splits=('train',)):
    """A completed index is immutable; use another root to change its scope."""
    root = Path(root)
    if root.exists():
        raise ValueError('Choose a fresh evidence index directory')
    if not splits or not set(splits) <= {'train', 'validation', 'test'}:
        raise ValueError('Invalid split scope')
    bp = Blueprint(dataset, expected)
    root.mkdir(parents=True)
    began = time.monotonic()
    db = sqlite3.connect(root / 'search.pending')
    db.execute('CREATE VIRTUAL TABLE passages USING fts5(document UNINDEXED, start UNINDEXED, end UNINDEXED, text, tokenize="unicode61 remove_diacritics 0")')
    count = documents = 0
    try:
        for row in bp.documents.values():
            if row['split'] not in splits:
                continue
            text = source_bytes(corpus, row['text_sha256']).decode('utf-8')
            db.executemany('INSERT INTO passages VALUES (?, ?, ?, ?)',
                           ((row['id'], start, end, part) for start, end, part in passages(text)))
            documents += 1
            if documents % 2000 == 0:
                db.commit()
                print(json.dumps({'phase': 'indexing', 'documents': documents}), flush=True)
        db.execute("INSERT INTO passages(passages) VALUES ('optimize')")
        db.commit()
        count = db.execute('SELECT count(*) FROM passages').fetchone()[0]
    finally:
        db.close()
    (root / 'search.pending').rename(root / 'search.sqlite')
    manifest = {'schema': 'hypertrix.evidence-index/1', 'blueprint': expected,
                'sources_sha256': bp.body['sources_sha256'], 'splits': sorted(set(splits)),
                'documents': documents, 'passages': count, 'method': METHOD,
                'sqlite_version': sqlite3.sqlite_version,
                'index_sha256': file_hash(root / 'search.sqlite'),
                'offset_unit': 'unicode-codepoint', 'passage_width': 1200,
                'build_seconds': time.monotonic() - began}
    write_json(root / 'index.json', manifest)
    return manifest


class Index:
    def __init__(self, root, dataset, corpus, expected):
        self.root, self.corpus = Path(root), Path(corpus)
        self.blueprint = Blueprint(dataset, expected)
        self.manifest = parse_json((self.root / 'index.json').read_bytes())
        if (self.manifest['schema'] != 'hypertrix.evidence-index/1'
                or self.manifest['blueprint'] != expected
                or self.manifest['sources_sha256'] != self.blueprint.body['sources_sha256']
                or self.manifest['method'] != METHOD
                or file_hash(self.root / 'search.sqlite') != self.manifest['index_sha256']):
            raise ValueError('Evidence index commitment differs')
        self.digests = {v['text_sha256'] for v in self.blueprint.documents.values()
                        if v['split'] in self.manifest['splits']}

    def search(self, query, limit=5):
        if not isinstance(query, str) or not 1 <= len(query) <= 2000 or not 1 <= limit <= 8:
            raise ValueError('Invalid search bounds')
        # Strip the FTS query language: source/query text never becomes SQL syntax.
        terms = list(dict.fromkeys(re.findall(r'\w+', query.lower(), re.UNICODE)))
        if not terms:
            return []
        terms = ([t for t in terms if t not in STOP_WORDS and len(t) > 1] or terms[:1])[:12]
        expression = ' OR '.join('"' + t + '"' for t in terms)
        uri = (self.root / 'search.sqlite').resolve().as_uri() + '?mode=ro&immutable=1'
        db = sqlite3.connect(uri, uri=True)
        try:
            # Bound work even for broad or adversarial queries.
            deadline = time.monotonic() + 3
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            rows = db.execute('SELECT document,start,end,text,bm25(passages) FROM passages WHERE passages MATCH ? ORDER BY rank LIMIT ?',
                              (expression, limit * 8)).fetchall()
        finally:
            db.close()
        results, seen = [], set()
        for document, start, end, text, rank in rows:
            row = self.blueprint.documents[document]
            if row['text_sha256'] in seen:
                continue
            seen.add(row['text_sha256'])
            results.append({'document': document, 'source_sha256': row['text_sha256'],
                            'start': int(start), 'end': int(end), 'text': text,
                            'passage_sha256': sha256(text.encode('utf-8')),
                            'retrieval_score': -rank})
            if len(results) == limit:
                break
        return results
