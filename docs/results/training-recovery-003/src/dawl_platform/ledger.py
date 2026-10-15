"""Replicated signed DAG, with per-author chains and explicit local trust policy.

There is no global consensus or total ordering. Replicas compare content sets;
author equivocation is rejected relative to each replica's retained history.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from dawl_node.common import canonical_json, parse_json, sha256, sign, verify_signature
from data_ingestion.protocol import require_digest

KINDS = {"resource": "source", "source": "source", "tokens": "tokenizer",
         "vectors": "vector", "audit": "auditor", "dataset": "source"}
MAX_ENTRY = 65536


class Ledger:
    def __init__(self, path: Path, trust: dict, read_only=False):
        self.path, self.trust, self.read_only = path, trust, read_only
        if trust.get("schema") != "dawl.trust/1" or not trust.get("identities"):
            raise ValueError("explicit identity policy required")
        for public, roles in trust["identities"].items():
            require_digest(public)
            if not roles or any(role not in set(KINDS.values()) for role in roles):
                raise ValueError("invalid identity role")
        self.policy = sha256(canonical_json(trust))
        if read_only:
            with closing(self.connect()) as db:
                if db.execute("SELECT digest FROM policy WHERE id=1").fetchone()[0] != self.policy:
                    raise ValueError("ledger policy differs from pinned trust")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS entries (
                    arrival INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, signer TEXT NOT NULL, sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL, envelope BLOB NOT NULL, UNIQUE(signer, sequence));
                CREATE TRIGGER IF NOT EXISTS no_entry_update BEFORE UPDATE ON entries
                    BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS no_entry_delete BEFORE DELETE ON entries
                    BEGIN SELECT RAISE(ABORT, 'append only'); END;
            """)
            db.execute("INSERT OR IGNORE INTO policy VALUES (1, ?)", (self.policy,))
            if db.execute("SELECT digest FROM policy WHERE id=1").fetchone()[0] != self.policy:
                raise ValueError("ledger identity policy changed; migration must be explicit")

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) if self.read_only else sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def validate(self, envelope):
        if len(canonical_json(envelope)) > MAX_ENTRY:
            raise ValueError("ledger entry exceeds byte limit")
        try:
            signer = envelope["body"]["signer"]
            body = verify_signature(envelope, signer)
            if set(body) != {"schema", "policy", "signer", "sequence", "previous", "kind", "parents", "payload"}:
                raise ValueError("invalid ledger fields")
            if body["schema"] != "dawl.lineage/1" or body["policy"] != self.policy:
                raise ValueError("wrong ledger protocol or pinned policy")
            if KINDS.get(body["kind"], "invalid") not in self.trust["identities"].get(signer, []):
                raise ValueError("signer not admitted for this role")
            if type(body["sequence"]) is not int or not 0 <= body["sequence"] < 2**31:
                raise ValueError("invalid author sequence")
            parents = body["parents"]
            if not isinstance(parents, list) or len(parents) > 64 or parents != sorted(set(parents)):
                raise ValueError("parents must be unique and sorted")
            for parent in parents:
                require_digest(parent)
            if body["previous"] is not None:
                require_digest(body["previous"])
            if not isinstance(body["payload"], dict):
                raise ValueError("invalid entry payload")
            return body
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("malformed ledger entry") from error

    def add(self, envelope):
        body = self.validate(envelope)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT envelope FROM entries WHERE id=?", (envelope["id"],)).fetchone()
            if existing:
                if parse_json(existing[0]) != envelope:
                    raise ValueError("same entry ID with different envelope")
                return False
            head = db.execute("SELECT id,sequence FROM entries WHERE signer=? ORDER BY sequence DESC LIMIT 1",
                              (body["signer"],)).fetchone()
            expected = (head[1] + 1, head[0]) if head else (0, None)
            if (body["sequence"], body["previous"]) != expected:
                raise ValueError("author history gap, rollback or equivocation")
            for parent in body["parents"]:
                if db.execute("SELECT 1 FROM entries WHERE id=?", (parent,)).fetchone() is None:
                    raise ValueError("missing lineage parent")
            db.execute("INSERT INTO entries(id,signer,sequence,kind,envelope) VALUES (?,?,?,?,?)",
                       (envelope["id"], body["signer"], body["sequence"], body["kind"], canonical_json(envelope)))
        return True

    def create(self, kind, payload, parents, key):
        public = key.public_key().public_bytes_raw().hex()
        with closing(self.connect()) as db:
            head = db.execute("SELECT id,sequence FROM entries WHERE signer=? ORDER BY sequence DESC LIMIT 1", (public,)).fetchone()
        envelope = sign({"schema": "dawl.lineage/1", "policy": self.policy,
                         "sequence": head[1] + 1 if head else 0, "previous": head[0] if head else None,
                         "kind": kind, "parents": sorted(set(parents)), "payload": payload}, key)
        self.add(envelope)
        return envelope

    def page(self, after=0, limit=64):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("invalid ledger cursor or limit")
        with closing(self.connect()) as db:
            rows = db.execute("SELECT arrival,envelope FROM entries WHERE arrival>? ORDER BY arrival LIMIT ?",
                              (after, limit)).fetchall()
        return {"after": after, "next": rows[-1][0] if rows else after,
                "entries": [parse_json(row[1]) for row in rows]}

    def entries(self):
        cursor = 0
        while True:
            page = self.page(cursor)
            if not page["entries"]:
                return
            yield from page["entries"]
            cursor = page["next"]

    def get(self, entry_id):
        require_digest(entry_id)
        with closing(self.connect()) as db:
            row = db.execute("SELECT envelope FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            raise ValueError("unknown lineage entry")
        envelope = parse_json(row[0])
        if envelope["id"] != entry_id:
            raise ValueError("stored ledger index does not match signed envelope")
        self.validate(envelope)
        return envelope

    def checkpoint(self):
        # This digest describes a content set, independently of arrival order.
        ids = sorted(entry["id"] for entry in self.entries())
        return {"policy": self.policy, "entries": len(ids), "set_sha256": sha256(canonical_json(ids))}

    def verify_history(self):
        # Replay into an independent transient database, checking every signature and link.
        import tempfile
        with closing(self.connect()) as db:
            for identity, signer, sequence, kind, blob in db.execute("SELECT id,signer,sequence,kind,envelope FROM entries"):
                envelope = parse_json(blob)
                body = self.validate(envelope)
                if (identity, signer, sequence, kind) != (envelope["id"], body["signer"], body["sequence"], body["kind"]):
                    raise ValueError("ledger index differs from signed history")
        with tempfile.TemporaryDirectory() as tmp:
            replay = Ledger(Path(tmp) / "verify.sqlite", self.trust)
            for entry in self.entries():
                replay.add(entry)
            return replay.checkpoint()
