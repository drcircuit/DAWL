"""Deploy one explicitly selected hat per HTTP process."""

from __future__ import annotations

from functools import lru_cache
import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .audit import audit_peer
from .store import StoredSnapshot


class ChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$")
    positions: list[int] = Field(min_length=1, max_length=256)


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples: int = Field(ge=1, le=256)


def _app(role: str, api_token: str) -> tuple[FastAPI, object]:
    if not api_token or len(api_token) < 24:
        raise ValueError("DAWL_API_TOKEN must contain at least 24 characters")
    app = FastAPI(title=f"DAWL {role} node", docs_url=None, redoc_url=None, openapi_url=None)

    async def authenticate(request: Request):
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + api_token).encode("utf-8")):
            raise HTTPException(401, "authentication required")

    @app.middleware("http")
    async def bounded_requests(request: Request, call_next):
        # Check the actual stream too, including requests with chunked transfer encoding.
        if request.method == "POST":
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > 16384:
                    return JSONResponse({"detail": "request exceeds 16384 bytes"}, status_code=413)
            request._body = bytes(data)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, error):
        return JSONResponse({"detail": "artifact unavailable"}, status_code=404)

    @app.get("/health")
    def health():
        return {"status": "ok", "role": role, "protocol": "dawl.ingestion/v2", "compute": "cpu"}

    return app, authenticate


def storage_app(root: Path, api_token: str) -> FastAPI:
    app, authenticate = _app("storage", api_token)
    root = root.resolve()

    @lru_cache(maxsize=2)
    def snapshot(snapshot_id: str):
        return StoredSnapshot(root, snapshot_id)

    @app.get("/v1/snapshots/{snapshot_id}/manifest", dependencies=[Depends(authenticate)])
    def manifest(snapshot_id: str):
        return snapshot(snapshot_id).envelope

    @app.get("/v1/snapshots/{snapshot_id}/files/{filename}", dependencies=[Depends(authenticate)])
    def file(snapshot_id: str, filename: str):
        stored = snapshot(snapshot_id)
        allowed = {f"{name}.{suffix}" for name in stored.shards for suffix in ("records.json", "u32le")}
        if filename not in allowed:
            raise HTTPException(404, "file is not a published token artifact")
        return FileResponse(stored.directory / filename, media_type="application/octet-stream")

    @app.post("/v1/snapshots/{snapshot_id}/challenge", dependencies=[Depends(authenticate)])
    def challenge(snapshot_id: str, body: ChallengeRequest):
        if snapshot_id != body.snapshot_id:
            raise HTTPException(400, "snapshot ID mismatch")
        return snapshot(snapshot_id).respond(body.nonce, body.positions)

    return app


def validator_app(peer, registry: dict, tokenizer, miner_public: str, key, api_token: str,
                  receipts: Path | None = None) -> FastAPI:
    app, authenticate = _app("validator", api_token)

    @app.post("/v1/audits", dependencies=[Depends(authenticate)])
    def audit(body: AuditRequest):
        receipt = audit_peer(peer, body.snapshot_id, registry, tokenizer, miner_public, body.samples, key)
        if receipts is not None:
            from .common import write_json
            write_json(receipts / f"{receipt['id']}.json", receipt)
        return receipt

    return app
