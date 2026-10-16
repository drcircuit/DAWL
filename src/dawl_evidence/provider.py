"""Separate evidence-provider service. Queries are processed without persisted logs."""
import sqlite3
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from data_ingestion.protocol import require_digest
from dawl_node.common import parse_json, sha256, sign
from .index import source_bytes, QUERY_METHOD


def app(index, key):
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slots = threading.BoundedSemaphore(2)

    @api.get('/health')
    def health():
        return {'role': 'EVIDENCE_PROVIDER', 'blueprint': index.blueprint.identity,
                'documents': index.manifest['documents'], 'passages': index.manifest['passages']}

    @api.get('/catalog')
    def catalog():
        return sign({'schema': 'hypertrix.evidence-catalog/1', 'index': index.manifest, 'search_method': QUERY_METHOD}, key)

    @api.post('/search')
    async def search(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 16384:
                raise HTTPException(413, 'Search exceeds request limit')
        try:
            body = parse_json(bytes(raw))
            if set(body) != {'query', 'blueprint', 'nonce', 'limit'}:
                raise ValueError('Unexpected search fields')
            require_digest(body['nonce'])
            if body['blueprint'] != index.blueprint.identity or type(body['limit']) is not int:
                raise ValueError('Blueprint or limit differs')
            if not isinstance(body['query'], str) or not 1 <= len(body['query']) <= 2000:
                raise ValueError('Invalid query')
            if not 1 <= body['limit'] <= 8:
                raise ValueError('Invalid result limit')
            if not slots.acquire(blocking=False):
                raise HTTPException(429, 'Search capacity occupied')
            began = time.monotonic()
            try:
                # Keep the event loop responsive while FTS runs on CPU.
                import asyncio
                hits = await asyncio.to_thread(index.search, body['query'], body['limit'])
            finally:
                slots.release()
            return sign({'schema': 'hypertrix.evidence-search/1', 'blueprint': index.blueprint.identity,
                         'index_sha256': index.manifest['index_sha256'], 'method': QUERY_METHOD,
                         'query_sha256': sha256(body['query'].encode('utf-8')), 'nonce': body['nonce'],
                         'issued': int(time.time()), 'splits': index.manifest['splits'],
                         'limit': body['limit'], 'hits': hits,
                         'elapsed_seconds': time.monotonic() - began}, key)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Invalid evidence request')
        except sqlite3.OperationalError:
            raise HTTPException(503, 'Search work budget exceeded')

    @api.get('/sources/{identity}')
    def source(identity: str):
        try:
            require_digest(identity)
            if identity not in index.digests:
                raise HTTPException(404, 'Source unavailable in index scope')
            raw = source_bytes(index.corpus, identity)
            return Response(raw, media_type='text/plain; charset=utf-8',
                            headers={'ETag': '"' + identity + '"', 'X-Content-Type-Options': 'nosniff'})
        except (ValueError, OSError):
            raise HTTPException(409, 'Source integrity check failed')

    return api
