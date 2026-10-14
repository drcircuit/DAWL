"""Signed, bounded profile snapshots, served through the existing CAS protocol."""
from pathlib import Path
from dawl_node.common import canonical_json, verify_signature, sign, immutable_write
from dawl_platform.ledger import Ledger
from .profiles import descriptor, validate_profile
from .workspace import ProfileLedger
from dawl_cluster.profile_protocol import validate_catalog


def catalog(ledger, blueprint_id):
    blueprint = ledger.get(blueprint_id); body = ledger.validate(blueprint); data = body['payload']['data']
    if body['kind'] != 'dataset' or set(data) != {'schema', 'profile_id', 'parent_blueprint', 'token_entries', 'books', 'frags', 'tokens', 'scope'}:
        raise ValueError('invalid token Blueprint')
    if data['schema'] != 'hypertrix.token-blueprint/1' or data['profile_id'] != ledger.profile['id']:
        raise ValueError('wrong Blueprint namespace')
    refs = data['token_entries']; parent = data['parent_blueprint']
    if not isinstance(refs, list) or not 1 <= len(refs) <= 32 or refs != sorted(set(refs)) or body['parents'] != sorted(refs+([parent] if parent else [])):
        raise ValueError('invalid Blueprint coverage')
    if parent:
        prior = ledger.get(parent); ledger.validate(prior)
        if prior['body']['kind'] != 'dataset' or not set(prior['body']['payload']['data']['token_entries']).issubset(refs):
            raise ValueError('Blueprint drops prior coverage')
    books = []
    for ref in refs:
        entry = ledger.get(ref); ledger.validate(entry); token = entry['body']['payload']['data']
        if entry['body']['kind'] != 'tokens' or entry['body']['parents'] != [token['source_entry']]:
            raise ValueError('invalid token lineage')
        source = ledger.get(token['source_entry']); ledger.validate(source); raw = source['body']['payload']['data']
        if source['body']['kind'] != 'source' or token['book_id'] != raw['book_id']: raise ValueError('wrong source lineage')
        descriptor(raw['artifact']); descriptor(token['manifest'])
        books.append({'book_id': token['book_id'], 'source_sha256': raw['artifact']['sha256'],
                      'tokens_entry': ref, 'frags': token['frags'], 'tokens': token['tokens']})
    books.sort(key=lambda b: b['book_id'])
    if [b['book_id'] for b in books] != data['books'] or sum(b['frags'] for b in books) != data['frags'] or sum(b['tokens'] for b in books) != data['tokens']:
        raise ValueError('Blueprint totals mismatch')
    result = {'schema': 'hypertrix.profile-catalog/1', 'profile_id': ledger.profile['id'],
              'blueprint': blueprint_id, 'checkpoint': ledger.checkpoint(), 'books': books}
    validate_catalog(result, ledger.profile['id'])
    return result


def export_bundle(workspace, ledger, tokenizer, blueprint, key):
    workspace.verify_blueprint(ledger, tokenizer, blueprint)
    description = catalog(ledger, blueprint['id'])
    envelope = sign({'schema': 'hypertrix.profile-bundle/1', 'profile': ledger.profile,
        'trust': ledger.trust, 'catalog': description, 'lineage': list(ledger.entries())}, key)
    if len(canonical_json(envelope)) > 4*1024*1024: raise ValueError('bundle exceeds limit')
    return {'profile_id': ledger.profile['id'], 'bundle': workspace.objects.json(envelope), 'catalog': description}


def import_bundle(root, envelope, contribution, profile):
    if len(canonical_json(envelope)) > 4*1024*1024: raise ValueError('bundle exceeds limit')
    body = verify_signature(envelope, contribution['provider'])
    if (set(body) != {'schema', 'signer', 'profile', 'trust', 'catalog', 'lineage'}
            or body['schema'] != 'hypertrix.profile-bundle/1' or body['profile'] != profile
            or body['catalog'] != contribution['catalog'] or contribution['profile_id'] != profile['id']):
        raise ValueError('bundle differs from certified profile contribution')
    validate_profile(profile); validate_catalog(body['catalog'], profile['id'])
    if not isinstance(body['lineage'], list) or not 3 <= len(body['lineage']) <= 256: raise ValueError('lineage exceeds bound')
    root = Path(root); ledger = ProfileLedger(root/'ledger.sqlite', body['trust'], profile)
    if ledger.policy != contribution['catalog']['checkpoint']['policy']: raise ValueError('wrong profile policy')
    for entry in body['lineage']: ledger.add(entry)
    if ledger.verify_history() != contribution['catalog']['checkpoint']: raise ValueError('incomplete lineage checkpoint')
    if catalog(ledger, body['catalog']['blueprint']) != body['catalog']: raise ValueError('catalog does not reproduce lineage')
    immutable_write(root/'profile.json', canonical_json(profile)); immutable_write(root/'trust.json', canonical_json(body['trust']))
    return ledger


def descriptors(value):
    """Extract only hash/length descriptors, never file paths or fetch destinations."""
    result = {}
    def visit(item):
        if isinstance(item, dict):
            if set(item) == {'sha256', 'bytes'}:
                descriptor(item)
                old = result.setdefault(item['sha256'], item)
                if old != item: raise ValueError('conflicting artifact lengths')
            else:
                for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    return result


def export_index(workspace, proposals):
    index = {}
    for proposal in proposals:
        index.update(descriptors(proposal['bundle']))
        envelope = workspace.objects.read_json(proposal['bundle'])
        direct = descriptors(envelope)
        index.update(direct)
        for entry in envelope['body']['lineage']:
            if entry['body']['kind'] == 'tokens':
                manifest = workspace.objects.read_json(entry['body']['payload']['data']['manifest'])
                index.update(descriptors(manifest))
    for item in index.values(): workspace.objects.read(item)
    return index
