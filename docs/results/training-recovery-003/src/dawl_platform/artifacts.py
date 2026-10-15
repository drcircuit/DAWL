"""Public hash-addressed data. No private keys are placed in this store."""
import hashlib
from pathlib import Path
from urllib.request import build_opener, ProxyHandler, Request
from urllib.parse import urlsplit

from dawl_node.common import canonical_json, immutable_write, parse_json, read_bytes, sha256
from dawl_node.transport import NoRedirects
from data_ingestion.protocol import require_digest

MAX_OBJECT = 256 * 1024 * 1024


class Objects:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, payload):
        if len(payload) > MAX_OBJECT:
            raise ValueError("artifact exceeds size cap")
        digest = sha256(payload)
        immutable_write(self.root / digest, payload)
        return {"sha256": digest, "bytes": len(payload)}

    def json(self, value):
        return self.put(canonical_json(value))

    def read(self, descriptor):
        digest = descriptor["sha256"]
        require_digest(digest)
        if type(descriptor["bytes"]) is not int or not 0 <= descriptor["bytes"] <= MAX_OBJECT:
            raise ValueError("invalid artifact size")
        payload = read_bytes(self.root / digest, descriptor["bytes"])
        if len(payload) != descriptor["bytes"] or sha256(payload) != digest:
            raise ValueError("artifact hash or size mismatch")
        return payload

    def read_json(self, descriptor):
        return parse_json(self.read(descriptor))

    def fetch(self, peer, descriptor):
        require_digest(descriptor["sha256"])
        path = self.root / descriptor["sha256"]
        if path.exists():
            self.read(descriptor)
            return path
        size = descriptor["bytes"]
        if type(size) is not int or not 0 <= size <= MAX_OBJECT:
            raise ValueError("invalid remote artifact size")
        payload = peer.request("/api/objects/" + descriptor["sha256"], limit=size)
        if len(payload) != size or sha256(payload) != descriptor["sha256"]:
            raise ValueError("download differs from committed artifact")
        self.put(payload)
        return path


class PublicPeer:
    def __init__(self, url, allow_insecure=False):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("peer must be an HTTP(S) origin")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1") and not allow_insecure:
            raise ValueError("remote plaintext requires explicit lab option")
        self.url = url.rstrip("/")
        self.opener = build_opener(NoRedirects(), ProxyHandler({}))

    def request(self, route, limit=8 * 1024 * 1024):
        with self.opener.open(Request(self.url + route), timeout=60) as response:
            payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("peer response exceeds size cap")
        return payload

    def json(self, route):
        return parse_json(self.request(route))


def sync(ledger, peer):
    remote = peer.json("/api/checkpoint")
    if remote["policy"] != ledger.policy:
        raise ValueError("peer uses a different pinned identity policy")
    cursor, added, received = 0, 0, set()
    # 100,000-entry operational ceiling; avoids an endless pagination response.
    for _ in range(1563):
        page = peer.json(f"/api/ledger?after={cursor}")
        if page.get("after") != cursor or not isinstance(page.get("entries"), list) or len(page["entries"]) > 64:
            raise ValueError("invalid ledger page")
        if not page["entries"]:
            if (len(received) != remote["entries"] or sha256(canonical_json(sorted(received))) != remote["set_sha256"]):
                raise ValueError("peer export differs from advertised checkpoint; incomplete export or concurrent change")
            return {"added": added, "remote_checkpoint": remote, **ledger.checkpoint()}
        if type(page.get("next")) is not int or page["next"] <= cursor:
            raise ValueError("peer cursor did not advance")
        for entry in page["entries"]:
            if entry["id"] in received:
                raise ValueError("peer repeated an entry while advancing its cursor")
            received.add(entry["id"])
            added += ledger.add(entry)
        cursor = page["next"]
    raise ValueError("ledger replication cap reached")
