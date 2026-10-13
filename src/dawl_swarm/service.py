"""Public read API and bounded, signed peer announcements. No remote job submission."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from data_ingestion.protocol import require_digest
from dawl_node.common import parse_json
from .node import Node, lineage_files
from .protocol import LEDGER_REPLICATOR, PROVIDER
from .transfer import PIECE, catalog


def app(root, key_path=None):
    node = Node(root, key_path)

    @asynccontextmanager
    async def lifespan(application):
        await node.start()
        try:
            yield
        finally:
            await node.close()

    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    application.state.node = node

    @application.middleware("http")
    async def friction(request, call_next):
        if request.url.path != "/swarm/status":
            fault = node.read_fault()
            if fault["partition"]:
                return JSONResponse({"error": "lab network partition"}, status_code=503)
            await asyncio.sleep(fault["delay_ms"] / 1000)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    @application.get("/health")
    def health():
        return {"status": "ok", "public": node.public}

    @application.get("/swarm/hello")
    def hello():
        return node.latest_announcement

    @application.post("/swarm/announce")
    async def announce(request: Request):
        payload = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(payload) + len(chunk) > 65536:
                    raise HTTPException(413, "announcement exceeds cap")
                payload.extend(chunk)
        try:
            envelope = parse_json(bytes(payload), 65536)
            node.state.peer(envelope)
            node.known.add(envelope["body"]["signer"])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, "invalid admitted announcement")
        return {"accepted": True}

    @application.get("/swarm/peers")
    def peers():
        import time
        return [node.latest_announcement] + [p for p in node.state.peers()
                if p["body"]["signer"] != node.public and p["body"]["expires_at"] > time.time()]

    @application.get("/swarm/status")
    def status():
        return node.status()

    @application.get("/api/checkpoint")
    def checkpoint():
        return node.ledger.checkpoint()

    @application.get("/api/ledger")
    def ledger(after: int = Query(default=0, ge=0)):
        if not node.roles & LEDGER_REPLICATOR:
            raise HTTPException(403, "node does not serve the replicator role")
        return node.ledger.page(after)

    @application.get("/swarm/observations")
    def observations(after: int = Query(default=0, ge=0)):
        return node.state.page(after)

    @application.get("/swarm/observations/{identity}")
    def observation(identity: str):
        require_digest(identity)
        return node.state.get(identity)["envelope"]

    def available(digest):
        require_digest(digest)
        if not node.roles & PROVIDER or node.fault["withhold"]:
            raise HTTPException(503, "artifact unavailable from this provider")
        expected = node.files.get(digest)
        if expected is None or not (node.objects.root / digest).is_file():
            raise HTTPException(404, "unknown or missing artifact")
        return expected

    @application.get("/swarm/catalog/{digest}")
    def listing(digest: str):
        expected = available(digest)
        if digest not in node.catalogs:
            node.catalogs[digest] = catalog(node.objects.root / digest, expected)
        return node.catalogs[digest]

    @application.get("/swarm/pieces/{digest}/{index}")
    def piece(digest: str, index: int):
        expected = available(digest)
        if index < 0 or index * PIECE >= expected["bytes"]:
            raise HTTPException(400, "piece outside artifact")
        node.served += 1
        if node.fault["drop_every"] and node.served % node.fault["drop_every"] == 0:
            raise HTTPException(503, "lab packet loss surrogate")
        with (node.objects.root / digest).open("rb") as stream:
            stream.seek(index * PIECE)
            payload = stream.read(min(PIECE, expected["bytes"] - index * PIECE))
        if node.fault["corrupt"] and payload:
            payload = bytes([payload[0] ^ 1]) + payload[1:]
        return Response(payload, media_type="application/octet-stream")

    return application
