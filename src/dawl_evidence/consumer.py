"""Consumer-controlled search, source verification and signed evidence collection.

Retrieval never labels a claim as supported. Semantic assessment is a separate,
versioned operation over the verified packet. No remote code is executed.
"""
from datetime import datetime, timezone
import secrets
import time

import httpx

from data_ingestion.protocol import require_digest
from dawl_node.common import canonical_json, parse_json, sha256, sign, verify_signature
from dawl_lab.mesh import route, validate
from .index import MAX_SOURCE_BYTES, METHOD, QUERY_METHOD


def bounded(client, method, url, limit, **kwargs):
    deadline = time.monotonic() + 20
    with client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        raw = bytearray()
        for block in response.iter_bytes():
            raw.extend(block)
            if len(raw) > limit or time.monotonic() > deadline:
                raise ValueError('Peer response exceeds byte or time bound')
        return bytes(raw)


def discover(client, endpoint, network, blueprint, allowed_signers):
    """DHT supplies routes, while a consumer-owned allowlist admits service identities."""
    route(endpoint, 8913)
    payload = bounded(client, 'GET', endpoint + '/dht/roles/EVIDENCE_PROVIDER', 256 * 1024)
    providers, seen = [], set()
    for record in parse_json(payload).get('records', []):
        try:
            body = validate(record, network)
            if ('EVIDENCE_PROVIDER' not in body['roles'] or blueprint not in body.get('profiles', [])
                    or body['signer'] not in allowed_signers or body['signer'] in seen):
                continue
            providers.append({'url': body['services']['evidence'], 'signer': body['signer']})
            seen.add(body['signer'])
            if len(providers) == 4:
                break
        except (ValueError, KeyError, TypeError):
            continue
    return providers


def verified_hits(envelope, public, blueprint, query, nonce, limit, fetch, now=None):
    body = verify_signature(envelope, public)
    if (body.get('schema') != 'hypertrix.evidence-search/1'
            or body['blueprint'] != blueprint.identity
            or body['query_sha256'] != sha256(query.encode('utf-8'))
            or body['nonce'] != nonce or body['limit'] != limit
            or body['method'] not in {METHOD, QUERY_METHOD}):
        raise ValueError('Search response binding differs')
    require_digest(body['index_sha256'])
    now = time.time() if now is None else now
    if type(body['issued']) is not int or not now - 120 <= body['issued'] <= now + 30:
        raise ValueError('Search receipt is stale')
    if not isinstance(body['hits'], list) or len(body['hits']) > limit:
        raise ValueError('Too many search hits')
    if not body['splits'] or not set(body['splits']) <= {'train', 'validation', 'test'}:
        raise ValueError('Invalid index scope')
    results = []
    for hit in body['hits']:
        document = blueprint.documents.get(hit['document'])
        if (document is None or hit['source_sha256'] != document['text_sha256']
                or document['split'] not in body['splits']):
            raise ValueError('Source is outside the pinned Blueprint or index scope')
        start, end = hit['start'], hit['end']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end
                or end - start > 1200 or not isinstance(hit['text'], str)):
            raise ValueError('Invalid passage offsets')
        raw = fetch(hit['source_sha256'])
        if len(raw) > MAX_SOURCE_BYTES or sha256(raw) != hit['source_sha256']:
            raise ValueError('Downloaded source hash differs')
        text = raw.decode('utf-8')
        if end > len(text) or text[start:end] != hit['text'] or sha256(hit['text'].encode('utf-8')) != hit['passage_sha256']:
            raise ValueError('Passage differs from committed source')
        # Origin and rights come from the committed manifest, not search-provider claims.
        results.append({'document': document['id'], 'source_sha256': hit['source_sha256'],
                        'start': start, 'end': end, 'text': hit['text'], 'passage_sha256': hit['passage_sha256'],
                        'title': document.get('title'), 'origin': document.get('origin'),
                        'rights': document.get('rights'), 'source_group': document.get('group'),
                        'split': document['split'], 'blueprint_membership': 'verified',
                        'passage_integrity': 'verified', 'training_exposure': 'not_checked',
                        'training_influence': 'not_assessed', 'semantic_support': 'not_assessed',
                        'confidence': None, 'providers': [public]})
    return results


def collect(answer, spans, checkpoint, blueprint, providers, key, client=None):
    """Spans are explicit selections from a frozen answer, including agent selections.

    This preserves the distinction between original output and later assessment.
    At most four peers/eight claims/three passages per peer are queried.
    """
    require_digest(checkpoint)
    if not isinstance(answer, str) or not 1 <= len(answer) <= 32000 or not 1 <= len(spans) <= 8:
        raise ValueError('Invalid answer or claim count')
    if not 1 <= len(providers) <= 4:
        raise ValueError('One to four admitted evidence providers required')
    claims = []
    starts = set()
    for span in spans:
        if (set(span) != {'start', 'end'} or any(type(span[k]) is not int for k in span)
                or not 0 <= span['start'] < span['end'] <= len(answer) or span['end'] - span['start'] > 2000
                or span['start'] in starts):
            raise ValueError('Invalid claim span')
        starts.add(span['start'])
        claims.append({**span, 'text': answer[span['start']:span['end']]})
    for peer in providers:
        route(peer['url'], 8914)
        require_digest(peer['signer'])
    owns_client = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
    cache, searches, failures, results = {}, [], [], []
    metrics = {'source_bytes_received': 0, 'source_requests': 0, 'source_cache_hits': 0, 'search_requests': 0}
    began = time.monotonic()
    try:
        for claim in claims:
            candidates = {}
            for peer in providers:
                # A bounded total budget leaves failed/unqueried peers visible.
                if time.monotonic() - began > 45:
                    failures.append({'claim_start': claim['start'], 'provider': peer['signer'], 'reason': 'budget_exhausted'})
                    continue
                nonce = secrets.token_hex(32)
                request = {'query': claim['text'], 'blueprint': blueprint.identity, 'nonce': nonce, 'limit': 3}
                def fetch(digest):
                    if digest not in cache:
                        raw = bounded(client, 'GET', peer['url'] + '/sources/' + digest, MAX_SOURCE_BYTES)
                        metrics['source_bytes_received'] += len(raw)
                        metrics['source_requests'] += 1
                        if sha256(raw) != digest:
                            raise ValueError('Downloaded source hash differs')
                        if len(cache) >= 4:
                            cache.pop(next(iter(cache)))
                        cache[digest] = raw
                    else:
                        metrics['source_cache_hits'] += 1
                    return cache[digest]
                try:
                    metrics['search_requests'] += 1
                    raw = bounded(client, 'POST', peer['url'] + '/search', 256 * 1024,
                                  content=canonical_json(request), headers={'Content-Type': 'application/json'})
                    envelope = parse_json(raw)
                    hits = verified_hits(envelope, peer['signer'], blueprint, claim['text'], nonce, 3, fetch)
                    searches.append({'claim_start': claim['start'], 'request': request, 'response': envelope})
                    for hit in hits:
                        identity = (hit['source_sha256'], hit['start'], hit['end'])
                        if identity not in candidates:
                            candidates[identity] = hit
                        elif peer['signer'] not in candidates[identity]['providers']:
                            candidates[identity]['providers'].append(peer['signer'])
                except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError):
                    failures.append({'claim_start': claim['start'], 'provider': peer['signer'], 'reason': 'unavailable_or_invalid'})
            results.append({**claim, 'candidates': list(candidates.values()), 'assessment': 'pending',
                            'retrieval_status': 'candidates_found' if candidates else 'no_verified_candidates'})
    finally:
        if owns_client:
            client.close()
    return sign({'schema': 'hypertrix.evidence-collection/1',
                 'created_at': datetime.now(timezone.utc).isoformat(),
                 'answer_sha256': sha256(answer.encode('utf-8')), 'checkpoint': checkpoint,
                 'checkpoint_binding': 'consumer_asserted', 'blueprint': blueprint.identity,
                 'sources_sha256': blueprint.body['sources_sha256'],
                 'claim_selection': 'explicit_answer_spans', 'claims': results,
                 'admitted_providers': sorted({p['signer'] for p in providers}),
                 'searches': searches, 'failures': failures, 'elapsed_seconds': time.monotonic() - began,
                 'metrics': metrics,
                 'assessment_policy': 'Retrieval candidates only; no semantic verdict or probability.'}, key)


def verify_collection(envelope, consumer_public, blueprint, answer, fetch):
    """Offline verification of recorded evidence, without repeating mutable search.

    Source integrity and membership are rechecked, not inferred from a signature.
    Historical response freshness is judged at the signed collection time.
    """
    body = verify_signature(envelope, consumer_public)
    if (body.get('schema') != 'hypertrix.evidence-collection/1'
            or body['blueprint'] != blueprint.identity
            or body['sources_sha256'] != blueprint.body['sources_sha256']
            or body['answer_sha256'] != sha256(answer.encode('utf-8'))
            or body['checkpoint_binding'] != 'consumer_asserted'
            or not 1 <= len(body['claims']) <= 8 or len(body['searches']) > 32):
        raise ValueError('Evidence collection binding differs')
    require_digest(body['checkpoint'])
    now = datetime.fromisoformat(body['created_at']).timestamp()
    claims = {}
    for claim in body['claims']:
        start, end = claim['start'], claim['end']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(answer)
                or claim['text'] != answer[start:end] or start in claims or claim['assessment'] != 'pending'):
            raise ValueError('Claim differs from original answer')
        claims[start] = {'claim': claim, 'candidates': {}}
    for search in body['searches']:
        slot = claims[search['claim_start']]
        request, response = search['request'], search['response']
        if request['query'] != slot['claim']['text'] or request['blueprint'] != blueprint.identity or request['limit'] != 3:
            raise ValueError('Search differs from claim')
        public = response['body']['signer']
        if public not in body['admitted_providers']:
            raise ValueError('Provider was not admitted by the consumer')
        for hit in verified_hits(response, public, blueprint, request['query'], request['nonce'], 3, fetch, now):
            identity = (hit['source_sha256'], hit['start'], hit['end'])
            if identity not in slot['candidates']:
                slot['candidates'][identity] = hit
            elif public not in slot['candidates'][identity]['providers']:
                slot['candidates'][identity]['providers'].append(public)
    for slot in claims.values():
        candidates = list(slot['candidates'].values())
        if slot['claim']['candidates'] != candidates:
            raise ValueError('Recorded candidate assessment or provenance differs')
        expected = 'candidates_found' if candidates else 'no_verified_candidates'
        if slot['claim']['retrieval_status'] != expected:
            raise ValueError('Recorded retrieval outcome differs')
    return {'schema': 'hypertrix.evidence-verification/1', 'collection': envelope['id'],
            'verified': True, 'claims': len(claims),
            'passages': sum(len(v['candidates']) for v in claims.values()),
            'scope': 'Answer binding, signatures, source hashes, exact excerpts and Blueprint membership.',
            'semantic_support': 'not_assessed', 'checkpoint_binding': 'consumer_asserted'}
