"""Durable ABCI state machine. Finalize is tentative; only Commit writes state."""
import base64
import copy
from contextlib import closing
from pathlib import Path
import sqlite3
import threading

from fastapi import FastAPI, Request, HTTPException
from dawl_node.common import canonical_json, parse_json, read_json, sha256
from .state import initial, advance, evaluate, MAX_TX
from .incidents import consensus_reports


class Application:
    def __init__(self, root):
        self.root = Path(root)
        self.plan = read_json(self.root / "plan.json")
        self.lock = threading.RLock()
        self.path = self.root / "application.sqlite"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),height INTEGER NOT NULL,payload BLOB NOT NULL,hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints(height INTEGER PRIMARY KEY,hash TEXT NOT NULL);
            """)
            row = db.execute("SELECT height,payload,hash FROM state WHERE id=1").fetchone()
        self.state = parse_json(row[1]) if row else initial(self.plan)
        if row and (row[0] != self.state["height"] or row[2] != sha256(canonical_json(self.state))):
            raise ValueError("persisted application state hash mismatch")
        if self.state["plan_sha256"] != sha256(canonical_json(self.plan)):
            raise ValueError("application plan changed; explicit new chain required")
        self.pending = None

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=20000")
        return db

    @staticmethod
    def app_hash(state):
        return base64.b64encode(bytes.fromhex(sha256(canonical_json(state)))).decode()

    def info(self):
        with self.lock:
            return {"height": self.state["height"], "hash": self.app_hash(self.state)}

    def init(self, chain_id, app_state):
        supplied = parse_json(base64.b64decode(app_state, validate=True))
        if chain_id != self.plan["chain_id"] or supplied != self.plan:
            raise ValueError("genesis and locally pinned application plan differ")
        return {"hash": self.app_hash(initial(self.plan))}

    def check(self, payload):
        with self.lock:
            state = copy.deepcopy(self.state)
            advance(state, state["height"]+1)
            try:
                tx = parse_json(base64.b64decode(payload, validate=True), MAX_TX)
                _, result = evaluate(state, tx)
                return {"code": result["code"], "log": result["log"]}
            except (ValueError, TypeError):
                return {"code": 1, "log": "invalid transaction encoding"}

    def finalize(self, height, txs, misbehavior=None):
        with self.lock:
            state = copy.deepcopy(self.state)
            advance(state, height)
            consensus_reports(state,misbehavior)
            results = []
            for payload in txs or []:
                try:
                    tx = parse_json(base64.b64decode(payload, validate=True), MAX_TX)
                    state, result = evaluate(state, tx)
                except (ValueError, TypeError):
                    result = {"code": 1, "log": "invalid transaction encoding"}
                if result["code"]:
                    state["rejected_transactions"] += 1
                results.append({"code":result["code"], "log":result["log"], "gas_wanted":1, "gas_used":1})
            self.pending = state
            return {"hash": self.app_hash(state), "results": results}

    def commit(self):
        with self.lock:
            if self.pending is None:
                raise ValueError("commit without a finalized block")
            encoded = canonical_json(self.pending)
            digest = sha256(encoded)
            with closing(self.connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO state VALUES (1,?,?,?)", (self.pending["height"],encoded,digest))
                db.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?)", (self.pending["height"],digest))
            self.state, self.pending = self.pending, None
            return {"committed": self.state["height"]}

    def query(self, path, height=0, prove=False):
        with self.lock:
            if path != "/state" or height not in (0,self.state["height"]) or prove:
                return {"code": 1, "height": self.state["height"], "value": ""}
            # Full canonical state bytes can be checked against the next block's signed app hash.
            return {"code": 0, "height":self.state["height"],
                    "value":base64.b64encode(canonical_json(self.state)).decode()}


def app(root):
    machine = Application(root)
    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    api.state.machine = machine

    @api.get("/health")
    def health():
        return machine.info()

    @api.post("/{operation}")
    async def command(operation: str, request: Request):
        # This interface binds ONLY to loopback inside each validator container.
        data = bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>4*1024*1024:
                raise HTTPException(413,"ABCI request exceeds cap")
            data.extend(chunk)
        value = parse_json(bytes(data))
        if operation == "info": return machine.info()
        if operation == "init": return machine.init(value["chain_id"],value["app_state"])
        if operation == "check": return machine.check(value["tx"])
        if operation == "finalize": return machine.finalize(value["height"],value["txs"],value.get("misbehavior"))
        if operation == "commit": return machine.commit()
        if operation == "query": return machine.query(value["path"],value["height"],value["prove"])
        raise HTTPException(404,"unknown local ABCI operation")
    return api
