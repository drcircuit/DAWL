"""Coexisting local provenance ledgers over one content-addressed artifact pool.

Blueprints here release tokenized corpora only. They are not Oracle-certified
releases, vector audits or training checkpoints.
"""
from pathlib import Path

from dawl_node.common import (canonical_json, immutable_write, pack_ids, read_json,
                              sha256, unpack_ids)
from dawl_node.source import chunks
from dawl_platform.artifacts import Objects
from dawl_platform.ledger import Ledger
from .profiles import ProfileTokenizer, validate_profile


def source_frags(raw):
    """Preserve the pilot's 512-KiB parts and 4-KiB UTF-8-safe Frags."""
    raw.decode('utf-8', errors='strict')
    start = 0
    while start < len(raw):
        end = min(start + 524288, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80: end -= 1
        for _, payload in chunks(raw[start:end], 4096): yield payload
        start = end


class ProfileLedger(Ledger):
    def __init__(self, path, trust, profile, read_only=False):
        validate_profile(profile)
        self.profile = profile
        if trust.get('profile_id') != profile['id']:
            raise ValueError('ledger trust belongs to another model profile')
        super().__init__(path, trust, read_only)

    def validate(self, envelope):
        body = super().validate(envelope)
        payload = body['payload']
        if (set(payload) != {'profile_id', 'data'} or
                payload['profile_id'] != self.profile['id'] or not isinstance(payload['data'], dict)):
            raise ValueError('entry belongs to another model profile')
        return body

    def record(self, kind, data, parents, key):
        return self.create(kind, {'profile_id': self.profile['id'], 'data': data}, parents, key)


class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        self.objects = Objects(self.root / 'objects')

    def open_profile(self, profile, trust):
        body = validate_profile(profile)
        directory = self.root / 'profiles' / profile['id']
        immutable_write(directory / 'profile.json', canonical_json(profile))
        immutable_write(directory / 'trust.json', canonical_json(trust))
        ledger = ProfileLedger(directory / 'ledger.sqlite', trust, profile)
        tokenizer = ProfileTokenizer(profile, self.objects.read(body['tokenizer']['artifact']),
                                     self.objects.read(body['model']['config']) if body['model'] else None)
        return ledger, tokenizer

    def ingest_book(self, ledger, tokenizer, book_id, raw, key):
        if tokenizer.profile_id != ledger.profile['id']:
            raise ValueError('tokenizer and ledger profiles differ')
        if type(book_id) is not int or not 1 <= book_id <= 1_000_000 or not 1 <= len(raw) <= 8*1024*1024:
            raise ValueError('invalid bounded source book')
        if any(e['body']['kind'] == 'source' and e['body']['payload']['data'].get('book_id') == book_id
               for e in ledger.entries()):
            raise ValueError('book already exists in this ledger; use an explicit new source version')
        source = ledger.record('source', {'book_id': book_id, 'artifact': self.objects.put(raw)}, [], key)
        records = []
        for index, frag in enumerate(source_frags(raw)):
            normalized, ids = tokenizer.encode(frag.decode('utf-8'))
            records.append({'index': index, 'source': self.objects.put(frag),
                'normalized': self.objects.put(normalized.encode('utf-8')),
                'tokens': self.objects.put(pack_ids(ids)), 'count': len(ids)})
        manifest = {'schema': 'hypertrix.profile-tokens/1', 'profile_id': ledger.profile['id'],
                    'source_entry': source['id'], 'book_id': book_id, 'records': records}
        data = {'book_id': book_id, 'source_entry': source['id'], 'manifest': self.objects.json(manifest),
                'frags': len(records), 'tokens': sum(r['count'] for r in records)}
        return ledger.record('tokens', data, [source['id']], key)

    def verify_tokens(self, ledger, tokenizer, entry):
        body = ledger.validate(entry)
        if body['kind'] != 'tokens' or tokenizer.profile_id != ledger.profile['id']:
            raise ValueError('token entry or tokenizer profile mismatch')
        data = body['payload']['data']
        if set(data) != {'book_id', 'source_entry', 'manifest', 'frags', 'tokens'}:
            raise ValueError('invalid token entry')
        if body['parents'] != [data['source_entry']]: raise ValueError('source not bound as lineage parent')
        source = ledger.get(data['source_entry']); ledger.validate(source)
        sd = source['body']['payload']['data']
        if (source['body']['kind'] != 'source' or set(sd) != {'book_id', 'artifact'}
                or type(sd['book_id']) is not int or not 1 <= sd['book_id'] <= 1_000_000
                or sd['book_id'] != data['book_id'] or not 1 <= sd['artifact']['bytes'] <= 8*1024*1024):
            raise ValueError('wrong source lineage')
        raw = self.objects.read(sd['artifact'])
        frags = list(source_frags(raw))
        manifest = self.objects.read_json(data['manifest'])
        expected = {'schema': 'hypertrix.profile-tokens/1', 'profile_id': ledger.profile['id'],
                    'source_entry': source['id'], 'book_id': data['book_id']}
        if set(manifest) != {*expected, 'records'} or any(manifest[k] != v for k, v in expected.items()):
            raise ValueError('token manifest profile or source mismatch')
        records = manifest['records']
        if not isinstance(records, list) or len(records) != len(frags) or data['frags'] != len(frags):
            raise ValueError('incomplete source coverage')
        count, changes = 0, 0
        for index, (frag, record) in enumerate(zip(frags, records)):
            if set(record) != {'index', 'source', 'normalized', 'tokens', 'count'} or record['index'] != index:
                raise ValueError('invalid ordered Frag record')
            if self.objects.read(record['source']) != frag: raise ValueError('raw source Frag mismatch')
            normalized, ids = tokenizer.encode(frag.decode('utf-8'))
            if self.objects.read(record['normalized']) != normalized.encode('utf-8'):
                raise ValueError('declared normalization mismatch')
            if unpack_ids(self.objects.read(record['tokens'])) != ids or record['count'] != len(ids):
                raise ValueError('retokenization mismatch')
            changes += int(normalized.encode('utf-8') != frag); count += len(ids)
        if count != data['tokens']: raise ValueError('incorrect token total')
        return {'book_id': data['book_id'], 'frags': len(frags), 'tokens': count,
                'normalized_frags': changes, 'source_bytes': len(raw)}

    def publish(self, ledger, tokenizer, key, parent=None):
        entries = [e for e in ledger.entries() if e['body']['kind'] == 'tokens']
        if not 1 <= len(entries) <= 32: raise ValueError('bounded Blueprint needs 1..32 token entries')
        reports = [self.verify_tokens(ledger, tokenizer, e) for e in entries]
        if len({r['book_id'] for r in reports}) != len(reports): raise ValueError('duplicate Blueprint book')
        prior = [e for e in ledger.entries() if e['body']['kind'] == 'dataset']
        expected_parent = prior[-1]['id'] if prior else None
        if parent != expected_parent: raise ValueError('Blueprint must extend this profile latest release')
        refs = sorted(e['id'] for e in entries)
        data = {'schema': 'hypertrix.token-blueprint/1', 'profile_id': ledger.profile['id'],
                'parent_blueprint': parent, 'token_entries': refs, 'books': sorted(r['book_id'] for r in reports),
                'frags': sum(r['frags'] for r in reports), 'tokens': sum(r['tokens'] for r in reports),
                'scope': 'Locally replayed tokenized corpus; not Oracle-certified, vector-audited or training-ready.'}
        result = ledger.record('dataset', data, refs + ([parent] if parent else []), key)
        self.verify_blueprint(ledger, tokenizer, result)
        return result

    def verify_blueprint(self, ledger, tokenizer, blueprint):
        body = ledger.validate(blueprint); data = body['payload']['data']
        if body['kind'] != 'dataset' or data.get('schema') != 'hypertrix.token-blueprint/1' or data.get('profile_id') != ledger.profile['id']:
            raise ValueError('wrong Blueprint profile or kind')
        refs = data['token_entries']; parent = data['parent_blueprint']
        if not 1 <= len(refs) <= 32 or refs != sorted(set(refs)) or body['parents'] != sorted(refs + ([parent] if parent else [])):
            raise ValueError('Blueprint lineage mismatch')
        reports = [self.verify_tokens(ledger, tokenizer, ledger.get(ref)) for ref in refs]
        if parent:
            ancestor = ledger.get(parent); ledger.validate(ancestor)
            if ancestor['body']['kind'] != 'dataset': raise ValueError('Blueprint parent has wrong kind')
            previous = ancestor['body']['payload']['data']
            if previous.get('profile_id') != ledger.profile['id'] or not set(previous['token_entries']).issubset(refs):
                raise ValueError('Blueprint dropped prior profile coverage')
        if (sorted(r['book_id'] for r in reports) != data['books'] or len(set(data['books'])) != len(reports)
                or sum(r['frags'] for r in reports) != data['frags'] or sum(r['tokens'] for r in reports) != data['tokens']):
            raise ValueError('Blueprint totals mismatch')
        return {'accepted': True, 'books': len(reports), 'frags': data['frags'], 'tokens': data['tokens']}
