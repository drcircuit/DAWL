"""Durable local observations, retained challenges and signed receipt batches."""
from contextlib import closing
import sqlite3
import time
import secrets

from dawl_node.common import canonical_json, parse_json, sign, verify_signature
from .protocol import LEDGER_REPLICATOR, validate_policy, validate_receipt, verify_announcement


class State:
    def __init__(self, path, policy, ledger):
        self.path, self.policy, self.ledger = path, policy, ledger
        self.pin = validate_policy(policy)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS peers (public TEXT PRIMARY KEY, sequence INTEGER, envelope BLOB, seen REAL);
                CREATE TABLE IF NOT EXISTS messages (arrival INTEGER PRIMARY KEY, id TEXT UNIQUE, schema TEXT,
                    signer TEXT, epoch INTEGER, envelope BLOB, local INTEGER, UNIQUE(signer,epoch));
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, challenge BLOB, receipt TEXT, retry_at REAL);
                CREATE TABLE IF NOT EXISTS events (arrival INTEGER PRIMARY KEY, at REAL, kind TEXT, detail BLOB);
                CREATE TABLE IF NOT EXISTS policy (pin TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS stream (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS cursors (public TEXT PRIMARY KEY, stream TEXT, position INTEGER);
            """)
            old = db.execute("SELECT pin FROM policy").fetchone()
            if old and old[0] != self.pin:
                raise ValueError("swarm state policy changed")
            db.execute("INSERT OR IGNORE INTO policy VALUES (?)", (self.pin,))
            row = db.execute("SELECT id FROM stream").fetchone()
            self.stream_id = row[0] if row else secrets.token_hex(32)
            if not row:
                db.execute("INSERT INTO stream VALUES (?)", (self.stream_id,))

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def increment(self, key, count=1):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value", (key, count))
            return db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]

    def counters(self):
        with closing(self.connect()) as db:
            return dict(db.execute("SELECT key,value FROM meta"))

    def peer(self, envelope):
        body = verify_announcement(envelope, self.policy)
        with closing(self.connect()) as db, db:
            previous = db.execute("SELECT sequence,envelope FROM peers WHERE public=?", (body["signer"],)).fetchone()
            if previous and body["sequence"] <= previous[0]:
                if body["sequence"] == previous[0] and canonical_json(envelope) == previous[1]:
                    return False  # Repeated gossip must not refresh liveness.
                raise ValueError("replayed or equivocal announcement")
            db.execute("INSERT OR REPLACE INTO peers VALUES (?,?,?,?)",
                       (body["signer"], body["sequence"], canonical_json(envelope), time.time()))
        return True

    def peers(self):
        with closing(self.connect()) as db:
            return [parse_json(row[0]) for row in db.execute("SELECT envelope FROM peers ORDER BY public")]

    def event(self, kind, **detail):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO events(at,kind,detail) VALUES (?,?,?)", (time.time(), kind, canonical_json(detail)))
            db.execute("DELETE FROM events WHERE arrival <= (SELECT COALESCE(MAX(arrival),0)-500 FROM events)")

    def events(self, limit=30):
        with closing(self.connect()) as db:
            return [{"at": r[0], "kind": r[1], **parse_json(r[2])} for r in
                    db.execute("SELECT at,kind,detail FROM events ORDER BY arrival DESC LIMIT ?", (limit,))]

    def get(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT envelope,local FROM messages WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("unknown observation")
        return {"envelope": parse_json(row[0]), "locally_executed": bool(row[1])}

    def add(self, envelope, local=False):
        if len(canonical_json(envelope)) > 65536:
            raise ValueError("observation exceeds cap")
        with closing(self.connect()) as db:
            existing = db.execute("SELECT envelope FROM messages WHERE id=?", (envelope["id"],)).fetchone()
        if existing:
            if existing[0] != canonical_json(envelope):
                raise ValueError("observation id collision")
            return False
        body = envelope["body"]
        if body["schema"] == "dawl.swarm-audit/1":
            validate_receipt(envelope, self.policy, self.ledger)
            epoch = None
        elif body["schema"] == "dawl.swarm-epoch/1":
            if not self.policy["members"].get(body["signer"], {}).get("roles", 0) & LEDGER_REPLICATOR:
                raise ValueError("epoch signer lacks replicator admission")
            verify_signature(envelope, body["signer"])
            if (set(body) != {"schema", "signer", "policy", "sequence", "previous", "receipts"}
                    or body["policy"] != self.pin or type(body["sequence"]) is not int or body["sequence"] < 1
                    or not isinstance(body["receipts"], list) or not 1 <= len(body["receipts"]) <= 64
                    or body["receipts"] != sorted(set(body["receipts"]))):
                raise ValueError("invalid observation batch")
            epoch = body["sequence"]
            if epoch == 1:
                if body["previous"] is not None:
                    raise ValueError("invalid initial epoch")
            else:
                parent = self.get(body["previous"])["envelope"]["body"]
                if parent["schema"] != body["schema"] or parent["signer"] != body["signer"] or parent["sequence"] != epoch - 1:
                    raise ValueError("invalid epoch chain")
            for ref in body["receipts"]:
                if self.get(ref)["envelope"]["body"]["schema"] != "dawl.swarm-audit/1":
                    raise ValueError("batch needs receipt references")
        else:
            raise ValueError("unknown observation schema")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT envelope FROM messages WHERE id=?", (envelope["id"],)).fetchone()
            if existing:
                if existing[0] != canonical_json(envelope):
                    raise ValueError("observation id collision")
                return False
            if db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= 100000:
                raise ValueError("observation store capacity reached")
            try:
                db.execute("INSERT INTO messages(id,schema,signer,epoch,envelope,local) VALUES (?,?,?,?,?,?)",
                           (envelope["id"], body["schema"], body["signer"], epoch, canonical_json(envelope), int(local)))
            except sqlite3.IntegrityError as error:
                raise ValueError("equivocal epoch sequence") from error
        return True

    def page(self, after=0):
        if type(after) is not int or after < 0:
            raise ValueError("invalid observation cursor")
        with closing(self.connect()) as db:
            rows = db.execute("SELECT arrival,envelope FROM messages WHERE arrival>? ORDER BY arrival LIMIT 32", (after,)).fetchall()
        return {"stream": self.stream_id, "after": after, "next": rows[-1][0] if rows else after, "entries": [parse_json(r[1]) for r in rows]}

    def cursor(self, public):
        with closing(self.connect()) as db:
            row = db.execute("SELECT stream,position FROM cursors WHERE public=?", (public,)).fetchone()
        return row if row else (None, 0)

    def advance(self, public, stream, position):
        from data_ingestion.protocol import require_digest
        require_digest(stream)
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?)", (public, stream, position))

    def receipts(self):
        with closing(self.connect()) as db:
            return [{"envelope": parse_json(r[0]), "locally_executed": bool(r[1])} for r in db.execute(
                "SELECT envelope,local FROM messages WHERE schema='dawl.swarm-audit/1' ORDER BY arrival")]

    def batch(self, key):
        public = key.public_key().public_bytes_raw().hex()
        with closing(self.connect()) as db:
            prior = [parse_json(r[0]) for r in db.execute("SELECT envelope FROM messages WHERE signer=? AND epoch IS NOT NULL ORDER BY epoch", (public,))]
        batched = {ref for e in prior for ref in e["body"]["receipts"]}
        pending = sorted(r["envelope"]["id"] for r in self.receipts() if r["envelope"]["id"] not in batched)[:64]
        if not pending:
            return None
        entry = sign({"schema": "dawl.swarm-epoch/1", "policy": self.pin, "sequence": len(prior) + 1,
                      "previous": prior[-1]["id"] if prior else None, "receipts": pending}, key)
        self.add(entry, local=True)
        return entry

    def job(self, identity, challenge):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO jobs VALUES (?,?,NULL,0)", (identity, canonical_json(challenge)))
            row = db.execute("SELECT challenge,receipt,retry_at FROM jobs WHERE id=?", (identity,)).fetchone()
        return {"challenge": parse_json(row[0]), "receipt": row[1], "retry_at": row[2]}

    def schedule(self):
        with closing(self.connect()) as db:
            return {row[0]: (row[1] is not None, row[2]) for row in db.execute("SELECT id,receipt,retry_at FROM jobs")}

    def finish(self, identity, receipt):
        success = receipt["body"]["outcome"] in ("pass", "invalid")
        with closing(self.connect()) as db, db:
            db.execute("UPDATE jobs SET receipt=?,retry_at=? WHERE id=?",
                       (receipt["id"] if success else None, time.time() + 30, identity))
