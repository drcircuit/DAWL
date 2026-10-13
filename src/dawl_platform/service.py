"""Read-only public inspection surface; accepts neither jobs nor uploaded code."""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from dawl_node.common import canonical_json, read_json
from data_ingestion.protocol import require_digest
from .workflow import open_node


def summary(root):
    ledger, objects = open_node(root, read_only=True)
    entries = list(ledger.entries())
    by_kind = {}
    for entry in entries:
        by_kind.setdefault(entry["body"]["kind"], []).append(entry)
    books = []
    for source in by_kind.get("source", []):
        payload = source["body"]["payload"]
        tokens = next((e for e in by_kind.get("tokens", []) if e["body"]["payload"]["source_entry"] == source["id"]), None)
        vectors = next((e for e in by_kind.get("vectors", []) if tokens and e["body"]["payload"]["tokens_entry"] == tokens["id"]), None)
        audits = [e for e in by_kind.get("audit", []) if vectors and e["body"]["payload"]["vectors_entry"] == vectors["id"]]
        books.append({"book_id": payload["book_id"], "title": payload["title"], "source": source["id"],
                      "tokens_entry": tokens["id"] if tokens else None, "vectors_entry": vectors["id"] if vectors else None,
                      "audit_entry": audits[-1]["id"] if audits else None,
                      "accepted": audits[-1]["body"]["payload"]["accepted"] if audits else None,
                      "chunks": payload["chunks"], "source_bytes": payload["files"]["book"]["bytes"],
                      "tokens": tokens["body"]["payload"]["tokens"] if tokens else 0,
                      "dense_bytes": vectors["body"]["payload"]["dense_bytes_reconstructed"] if vectors else 0})
    runs = []
    for pattern in ("token-run-*.json", "vector-run-*.json"):
        for path in sorted(root.glob(pattern)):
            runs.append({"type": pattern.split("-")[0], "report": read_json(path)})
    benchmark = read_json(root / "benchmark.json") if (root / "benchmark.json").exists() else None
    return {"checkpoint": ledger.checkpoint(), "counts": {k: len(v) for k, v in by_kind.items()},
            "books": books, "identities": ledger.trust["names"], "runs": runs, "benchmark": benchmark,
            "dataset": by_kind.get("dataset", [None])[-1],
            "scope": "Local federation prototype. Publicly replayable lineage; admitted signing identities. No training yet."}


def app(root):
    root = root.resolve()
    ledger, objects = open_node(root, read_only=True)
    interface = Path(__file__).resolve().parents[1] / "platform_interface"
    ledger.verify_history()
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # No mutation routes or CORS. Serving the read-only API on the lab bridge is explicit in CLI.
    @application.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @application.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": str(error)}, status_code=400)

    @application.get("/health")
    def health():
        return {"status": "ok", "role": "lineage-and-artifact-peer", "policy": ledger.policy}

    @application.get("/api/checkpoint")
    def checkpoint():
        return ledger.checkpoint()

    @application.get("/api/ledger")
    def page(after: int = Query(default=0, ge=0)):
        return ledger.page(after)

    @application.get("/api/export")
    def export():
        payload = b"\n".join(canonical_json(entry) for entry in ledger.entries()) + b"\n"
        return Response(payload, media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="dawl-ledger.jsonl"'})

    @application.get("/api/trust")
    def trust():
        return {"warning": "Bootstrap keys must be pinned through an independent channel; this download alone is not trust.",
                "policy_sha256": ledger.policy, "policy": ledger.trust}

    @application.get("/api/summary")
    def state():
        return summary(root)

    @application.get("/api/entries/{entry_id}")
    def entry(entry_id: str):
        return ledger.get(entry_id)

    @application.get("/api/lineage/{entry_id}")
    def lineage(entry_id: str):
        pending, seen, entries = [entry_id], set(), []
        while pending:
            ref = pending.pop()
            if ref in seen:
                continue
            if len(seen) >= 256:
                raise ValueError("lineage exploration cap reached")
            seen.add(ref)
            envelope = ledger.get(ref)
            entries.append(envelope)
            pending.extend(envelope["body"]["parents"])
        return {"root": entry_id, "entries": entries}

    @application.get("/api/objects/{digest}")
    def artifact(digest: str):
        require_digest(digest)
        path = objects.root / digest
        if not path.is_file():
            raise HTTPException(404, "artifact unavailable on this peer")
        return FileResponse(path, media_type="application/octet-stream", filename=digest)

    @application.get("/")
    def index():
        return FileResponse(interface / "index.html")

    @application.get("/app.js")
    def javascript():
        return FileResponse(interface / "app.js", media_type="text/javascript")

    @application.get("/style.css")
    def css():
        return FileResponse(interface / "style.css", media_type="text/css")

    return application
