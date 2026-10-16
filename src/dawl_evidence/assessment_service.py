"""Bounded CPU Investigator service over admitted, signed consumer requests."""
import asyncio
import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Request

from dawl_lab.mesh import route, validate
from dawl_node.common import canonical_json, parse_json, sign, verify_signature
from data_ingestion.protocol import require_digest
from .consumer import bounded
from .index import MAX_SOURCE_BYTES
from .investigator import investigate, verify_assessment, POLICY, POLICY_ID


def discover(client, endpoint, network, blueprint, admitted, evaluator):
    route(endpoint, 8913)
    payload = bounded(client, 'GET', endpoint + '/dht/roles/EVIDENCE_ASSESSOR', 256 * 1024)
    result, seen = [], set()
    for record in parse_json(payload).get('records', []):
        try:
            body = validate(record, network)
            if ('EVIDENCE_ASSESSOR' not in body['roles'] or body['signer'] not in admitted
                    or body['signer'] in seen or blueprint not in body['profiles']
                    or body.get('assessment') != {'evaluator_sha256': evaluator, 'policy_sha256': POLICY_ID}):
                continue
            result.append({'url': body['services']['investigator'], 'signer': body['signer']})
            seen.add(body['signer'])
            if len(result) == 4:
                break
        except (KeyError, TypeError, ValueError):
            continue
    return result


def request_assessment(client, peer, collection, answer, evaluator, key, nonce):
    route(peer['url'], 8915)
    require_digest(nonce)
    request = sign({'schema': 'hypertrix.investigation-request/1', 'collection': collection,
                    'answer': answer, 'evaluator_sha256': evaluator, 'policy_sha256': POLICY_ID,
                    'nonce': nonce, 'issued': int(time.time())}, key)
    # Scoring is CPU bounded by pair/token limits. Allow the server time to finish;
    # the generic retrieval helper has a shorter deadline suited to source transfers.
    with client.stream('POST', peer['url'] + '/investigate', content=canonical_json(request), timeout=180) as response:
        response.raise_for_status()
        raw = bytearray()
        began = time.monotonic()
        for block in response.iter_bytes():
            raw.extend(block)
            if len(raw) > 1024 * 1024 or time.monotonic() - began > 180:
                raise ValueError('Investigation response exceeds bounds')
    receipt = parse_json(bytes(raw))
    verify_assessment(receipt, peer['signer'], collection, evaluator, nonce)
    return receipt


def app(blueprint, evaluator, key, admitted, fetch_factory):
    """fetch_factory returns a context manager for a server-configured source fetcher.

    Requests cannot choose network routes or load models. Answers are never rewritten.
    The service stores only a bounded, expiring nonce cache, not prompt/response logs.
    """
    api = FastAPI(docs_url=None, openapi_url=None, redoc_url=None)
    slot = threading.BoundedSemaphore(1)
    nonces = {}

    @api.get('/health')
    def health():
        return {'role': 'EVIDENCE_ASSESSOR', 'name': 'Investigator', 'blueprint': blueprint.identity,
                'evaluator_sha256': evaluator.identity, 'policy_sha256': POLICY_ID}

    @api.get('/policy')
    def policy():
        return sign({'schema': 'hypertrix.investigator-capability/1', 'evaluator': evaluator.descriptor,
                     'evaluator_sha256': evaluator.identity, 'policy': POLICY, 'policy_sha256': POLICY_ID}, key)

    @api.post('/investigate')
    async def assess(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise HTTPException(413, 'Investigation request exceeds limit')
        try:
            envelope = parse_json(bytes(raw))
            public = envelope['body']['signer']
            if public not in admitted:
                raise HTTPException(403, 'Consumer not admitted')
            body = verify_signature(envelope, public)
            fields = {'schema', 'signer', 'collection', 'answer', 'evaluator_sha256', 'policy_sha256', 'nonce', 'issued'}
            if (set(body) != fields or body['schema'] != 'hypertrix.investigation-request/1'
                    or body['evaluator_sha256'] != evaluator.identity or body['policy_sha256'] != POLICY_ID
                    or type(body['issued']) is not int or not time.time()-120 <= body['issued'] <= time.time()+30
                    or not isinstance(body['answer'], str) or not 1 <= len(body['answer']) <= 32000):
                raise ValueError('Request identity or lifetime differs')
            require_digest(body['nonce'])
            if not slot.acquire(blocking=False):
                raise HTTPException(429, 'Investigator busy')
            try:
                now = time.time()
                for identity, expiry in list(nonces.items()):
                    if expiry <= now:
                        del nonces[identity]
                identity = (public, body['nonce'])
                if identity in nonces:
                    raise HTTPException(409, 'Request nonce already used')
                if len(nonces) >= 256:
                    raise HTTPException(429, 'Request replay cache occupied')
                nonces[identity] = now + 180
                def run():
                    with fetch_factory() as fetch:
                        return investigate(body['collection'], public, blueprint, body['answer'], fetch,
                                           evaluator, key, body['nonce'])
                # A shielded job retains the single scoring slot until done even if
                # the connection disappears. No second inference may overlap it.
                job = asyncio.create_task(asyncio.to_thread(run))
                try:
                    return await asyncio.shield(job)
                except asyncio.CancelledError:
                    await job
                    raise
            finally:
                slot.release()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Invalid investigation request or evidence')
        except (OSError, httpx.HTTPError):
            raise HTTPException(503, 'Committed source unavailable')

    return api


def main():
    import argparse
    from contextlib import contextmanager
    import json
    from pathlib import Path
    import uvicorn
    from dawl_node.common import keygen, load_key
    from dawl_lab.mesh import configure_policy
    from .index import Blueprint
    from .investigator import NliEvaluator
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', type=Path, required=True)
    p.add_argument('--blueprint', required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--model-pin', required=True)
    p.add_argument('--identity', type=Path, required=True)
    p.add_argument('--admit', action='append', required=True)
    p.add_argument('--network', type=Path, required=True)
    p.add_argument('--network-id', required=True)
    p.add_argument('--provider', required=True)
    p.add_argument('--bind', default='127.0.0.1')
    a = p.parse_args()
    configure_policy(json.loads(a.network.read_bytes()), a.network_id)
    route(a.provider, 8914)
    for public in a.admit:
        require_digest(public)
    a.identity.mkdir(parents=True, exist_ok=True)
    private = a.identity / 'key.private.pem'
    if not private.exists():
        keygen(private, a.identity / 'key.public.hex')
    @contextmanager
    def sources():
        cache = {}
        began = time.monotonic()
        with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
            def fetch(digest):
                require_digest(digest)
                if time.monotonic() - began > 60:
                    raise ValueError('Source verification budget exhausted')
                if digest not in cache:
                    value = bounded(client, 'GET', a.provider + '/sources/' + digest, MAX_SOURCE_BYTES)
                    if len(cache) >= 4:
                        cache.pop(next(iter(cache)))
                    cache[digest] = value
                return cache[digest]
            yield fetch
    api = app(Blueprint(a.dataset, a.blueprint), NliEvaluator(a.model, a.model_pin),
              load_key(private), set(a.admit), sources)
    uvicorn.run(api, host=a.bind, port=8915, access_log=False, limit_concurrency=8)


if __name__ == '__main__':
    main()
