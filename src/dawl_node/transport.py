"""Bounded HTTP transport and independently verified consumer replication."""

from __future__ import annotations

from pathlib import Path
from http.client import HTTPException
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from data_ingestion.protocol import canonical_json, require_digest
from .common import MAX_JSON, parse_json, staging_directory, write_json
from .store import validate_manifest, verify_snapshot


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("peer redirects are not allowed")


class PeerClient:
    def __init__(self, base_url: str, token: str, *, allow_insecure: bool = False):
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("peer URL must be an HTTP(S) origin without credentials, query, or fragment")
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme == "http" and not local and not allow_insecure:
            raise ValueError("non-loopback peers require HTTPS or explicit --allow-insecure-lab")
        if not token or len(token) < 24 or any(char in token for char in "\r\n"):
            raise ValueError("peer API token must contain at least 24 characters")
        self.base_url, self.token = base_url.rstrip("/"), token
        self.opener = build_opener(NoRedirects(), ProxyHandler({}))

    def request(self, route: str, payload=None, limit: int = MAX_JSON) -> bytes:
        request = Request(self.base_url + route, data=canonical_json(payload) if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                value = response.read(limit + 1)
                if len(value) > limit:
                    raise ValueError("peer response exceeded its size limit")
                return value
        except (OSError, HTTPException) as error:
            # Do not include credentials or source text in diagnostics.
            raise ValueError(f"peer request failed: {getattr(error, 'code', type(error).__name__)}") from error

    def manifest(self, snapshot_id: str) -> dict:
        require_digest(snapshot_id)
        return parse_json(self.request(f"/v1/snapshots/{snapshot_id}/manifest"))

    def challenge(self, snapshot_id: str, challenge: dict) -> tuple[dict, int]:
        require_digest(snapshot_id)
        payload = self.request(f"/v1/snapshots/{snapshot_id}/challenge", challenge)
        return parse_json(payload), len(payload)


def consume(peer: PeerClient, snapshot_id: str, registry: dict, tokenizer, public: str, root: Path) -> dict:
    """Consumer copies bounded shards and checks every byte before publication."""
    envelope = peer.manifest(snapshot_id)
    if envelope["id"] != snapshot_id:
        raise ValueError("peer returned a different snapshot")
    body = validate_manifest(envelope, registry, tokenizer, public)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".staging").mkdir(exist_ok=True)
    (root / "snapshots").mkdir(exist_ok=True)
    destination = root / "snapshots" / snapshot_id
    if destination.exists():
        return verify_snapshot(destination, registry, tokenizer, public)
    with staging_directory(root / ".staging", "consume-") as temporary:
        directory = temporary / snapshot_id
        directory.mkdir()
        write_json(directory / "manifest.json", envelope)
        write_json(directory / "source.json", registry)
        (directory / "tokenizer.json").write_bytes(tokenizer.payload)
        for shard in body["shards"]:
            for suffix, limit in (("records.json", 128 * 1024), ("u32le", shard["token_count"] * 4)):
                name = f"{shard['name']}.{suffix}"
                (directory / name).write_bytes(peer.request(f"/v1/snapshots/{snapshot_id}/files/{name}", limit=limit))
        result = verify_snapshot(directory, registry, tokenizer, public)
        try:
            directory.rename(destination)
        except FileExistsError:
            result = verify_snapshot(destination, registry, tokenizer, public)
    return result
