"""Read-only content-addressed provider for corpus objects and sharded checkpoints."""
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from dawl_node.common import read_json
from dawl_platform.workflow import open_node
from dawl_swarm.transfer import PIECE, catalog
from data_ingestion.protocol import require_digest


def app(root):
    ledger,objects=open_node(root,read_only=True)
    index=read_json(root/"artifact-index.json")
    catalogs={}
    api=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    def artifact(digest):
        require_digest(digest)
        if digest not in index: raise HTTPException(404,"artifact is outside the published set")
        return index[digest]
    @api.get("/health")
    def health(): return {"role":"provider","artifacts":len(index)}
    @api.get("/api/checkpoint")
    def checkpoint(): return ledger.checkpoint()
    @api.get("/api/ledger")
    def entries(after:int=Query(default=0,ge=0)): return ledger.page(after)
    @api.get("/api/objects/{digest}")
    def whole(digest:str):
        artifact(digest)
        return FileResponse(objects.root/digest,media_type="application/octet-stream")
    @api.get("/swarm/catalog/{digest}")
    def listing(digest:str):
        expected=artifact(digest)
        if digest not in catalogs: catalogs[digest]=catalog(objects.root/digest,expected)
        return catalogs[digest]
    @api.get("/swarm/pieces/{digest}/{index}")
    def piece(digest:str,index:int):
        expected=artifact(digest)
        if index<0 or index*PIECE>=expected["bytes"]: raise HTTPException(400,"piece outside artifact")
        with (objects.root/digest).open("rb") as stream:
            stream.seek(index*PIECE)
            payload=stream.read(min(PIECE,expected["bytes"]-index*PIECE))
        return Response(payload,media_type="application/octet-stream")
    return api
