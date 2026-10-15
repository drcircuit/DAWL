"""Bounded asynchronous pull; untrusted piece catalogs never replace whole-object pins."""
import asyncio
import hashlib
import math
from pathlib import Path

import httpx

from dawl_node.common import immutable_write, parse_json, read_bytes, sha256
from dawl_platform.artifacts import MAX_OBJECT
from data_ingestion.protocol import require_digest

PIECE = 1024 * 1024


class Unavailable(OSError):
    pass


def descriptor(value):
    if set(value) != {"sha256", "bytes"} or type(value["bytes"]) is not int or not 1 <= value["bytes"] <= MAX_OBJECT:
        raise ValueError("invalid transfer descriptor")
    require_digest(value["sha256"])
    return value


def catalog(path, expected):
    descriptor(expected)
    hashes, whole, count = [], hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(PIECE):
            hashes.append(sha256(chunk))
            whole.update(chunk)
            count += len(chunk)
    if count != expected["bytes"] or whole.hexdigest() != expected["sha256"]:
        raise ValueError("provider artifact differs from ledger")
    return {"artifact": expected, "piece_bytes": PIECE, "pieces": hashes}


class Puller:
    def __init__(self, client, objects, state, origins, concurrency=4):
        self.client, self.objects, self.state = client, objects, state
        self.origins = frozenset(origins)  # No addresses from fetched data ever become request targets.
        self.limit = asyncio.Semaphore(concurrency)
        self.pieces = objects.root.parent / "pieces"
        self.pieces.mkdir(exist_ok=True)
        self.locks = {}  # Only descriptors from the bounded, admitted lineage are scheduled.

    async def request(self, endpoint, route, limit=4 * 1024 * 1024, payload=None):
        if endpoint not in self.origins or not route.startswith("/") or route.startswith("//"):
            raise ValueError("request endpoint is outside pinned admission")
        async with self.limit, asyncio.timeout(8):
            async with self.client.stream("POST" if payload is not None else "GET", endpoint + route,
                                          content=payload, headers={"Accept-Encoding": "identity", "Content-Type": "application/json"}) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("compressed peer responses are not accepted")
                buffer = bytearray()
                async for block in response.aiter_raw():
                    if len(buffer) + len(block) > limit:
                        raise ValueError("peer response exceeds byte cap")
                    buffer.extend(block)
                self.state.increment("received_bytes", len(buffer))
                self.state.increment("requests")
                return bytes(buffer)

    async def json(self, endpoint, route):
        return parse_json(await self.request(endpoint, route))

    async def fetch(self, expected, providers):
        descriptor(expected)
        digest = expected["sha256"]
        async with self.locks.setdefault(digest, asyncio.Lock()):
            path = self.objects.root / digest
            if path.exists():
                await asyncio.to_thread(self.objects.read, expected)
                self.state.increment("object_cache_hits")
                return path
            errors = []
            for endpoint in providers:
                try:
                    listing = await self.json(endpoint, "/swarm/catalog/" + digest)
                    if (set(listing) != {"artifact", "piece_bytes", "pieces"} or listing["artifact"] != expected
                            or listing["piece_bytes"] != PIECE or not isinstance(listing["pieces"], list)
                            or len(listing["pieces"]) != math.ceil(expected["bytes"] / PIECE)):
                        raise ValueError("invalid piece catalog")
                    for piece in listing["pieces"]:
                        require_digest(piece)
                    # Four workers at a time; never creates one task per corpus piece.
                    for first in range(0, len(listing["pieces"]), 4):
                        async with asyncio.TaskGroup() as group:
                            for index in range(first, min(first + 4, len(listing["pieces"]))):
                                group.create_task(self.fetch_piece(expected, listing["pieces"][index], index,
                                                                 [endpoint] + [p for p in providers if p != endpoint]))
                    def assemble():
                        payload = bytearray()
                        for piece in listing["pieces"]:
                            payload.extend(read_bytes(self.pieces / piece, PIECE))
                        if len(payload) != expected["bytes"] or sha256(payload) != digest:
                            raise ValueError("catalog assembly differs from pinned whole-object hash")
                        self.objects.put(bytes(payload))
                    await asyncio.to_thread(assemble)
                    self.state.increment("objects_downloaded")
                    self.state.event("artifact_complete", sha256=digest, bytes=expected["bytes"])
                    return path
                except (httpx.HTTPError, TimeoutError, OSError, ValueError, ExceptionGroup) as error:
                    errors.append(f"{endpoint}: {str(error)[:160]}")
                    self.state.event("provider_failed", endpoint=endpoint, artifact=digest, error=str(error)[:200])
            raise Unavailable("No peer delivered the committed artifact: " + "; ".join(errors)[:700])

    async def fetch_piece(self, expected, digest, index, providers):
        size = min(PIECE, expected["bytes"] - index * PIECE)
        path = self.pieces / digest
        if path.exists():
            payload = await asyncio.to_thread(read_bytes, path, PIECE)
            if len(payload) != size or sha256(payload) != digest:
                raise ValueError("local piece cache is corrupt")
            self.state.increment("piece_cache_hits")
            return
        for endpoint in providers:
            try:
                payload = await self.request(endpoint, f"/swarm/pieces/{expected['sha256']}/{index}", size)
                if len(payload) != size or sha256(payload) != digest:
                    self.state.increment("corrupt_pieces_rejected")
                    raise ValueError("piece hash or size mismatch")
                await asyncio.to_thread(immutable_write, path, payload)
                self.state.increment("pieces_downloaded")
                return
            except (httpx.HTTPError, TimeoutError, OSError, ValueError) as error:
                self.state.increment("piece_attempt_failures")
                self.state.event("piece_rejected", endpoint=endpoint, artifact=expected["sha256"], index=index, error=str(error)[:160])
        raise Unavailable(f"piece {index} unavailable from admitted providers")
