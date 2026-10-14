"""Read-only profile CAS provider. The public index never exposes private keys."""
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from dawl_node.common import read_json
from dawl_platform.artifacts import Objects
from dawl_swarm.transfer import catalog, PIECE
from data_ingestion.protocol import require_digest


def app(root):
    root = Path(root); objects = Objects(root/'objects'); index = read_json(root/'artifact-index.json')
    api = FastAPI(docs_url=None, openapi_url=None, redoc_url=None); catalogs = {}
    def expected(digest):
        try: require_digest(digest)
        except ValueError: raise HTTPException(400, 'invalid object ID')
        if digest not in index: raise HTTPException(404, 'unpublished object')
        return index[digest]
    @api.get('/health')
    def health(): return {'role': 'profile-provider', 'objects': len(index)}
    @api.get('/api/objects/{digest}')
    def whole(digest: str):
        expected(digest); return FileResponse(objects.root/digest, media_type='application/octet-stream')
    @api.get('/swarm/catalog/{digest}')
    def listing(digest: str):
        desc = expected(digest)
        if digest not in catalogs: catalogs[digest] = catalog(objects.root/digest, desc)
        return catalogs[digest]
    @api.get('/swarm/pieces/{digest}/{position}')
    def piece(digest: str, position: int):
        desc = expected(digest)
        if position < 0 or position*PIECE >= desc['bytes']: raise HTTPException(400, 'invalid piece')
        with (objects.root/digest).open('rb') as stream:
            stream.seek(position*PIECE); data = stream.read(min(PIECE, desc['bytes']-position*PIECE))
        return Response(data, media_type='application/octet-stream')
    return api
