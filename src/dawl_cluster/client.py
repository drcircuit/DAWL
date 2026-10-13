"""Failover RPC client with a durable signed outbox and per-actor sequencing."""
import asyncio
import base64
from contextlib import closing
from pathlib import Path
import sqlite3
import time

import httpx
from dawl_node.common import canonical_json, parse_json, read_json, sign


class Client:
    def __init__(self, root, key=None):
        self.root, self.key = Path(root), key
        self.config = read_json(self.root / "cluster.json")
        self.plan = read_json(self.root / "plan.json")
        self.public = key.public_key().public_bytes_raw().hex() if key else None
        self.http = httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False)
        self.serial = asyncio.Lock()
        self.path = self.root / "worker.sqlite"
        with closing(self.connect()) as db, db:
            db.executescript("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY CHECK(id=1),payload BLOB); CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY,value INTEGER); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload BLOB);")
        self.cursor = 0

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def increment(self, name, amount=1):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(name,amount))

    def event(self, kind, **fields):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO events(payload) VALUES (?)", (canonical_json({"time":time.time(),"kind":kind,**fields}),))
            db.execute("DELETE FROM events WHERE id < (SELECT MAX(id)-256 FROM events)")

    async def rpc(self, method, params, endpoint=None):
        endpoints = [endpoint] if endpoint else self.config["rpcs"][self.cursor:] + self.config["rpcs"][:self.cursor]
        errors=[]
        for url in endpoints:
            try:
                async with self.http.stream("POST",url,json={"jsonrpc":"2.0","id":1,"method":method,"params":params}) as response:
                    response.raise_for_status()
                    data=bytearray()
                    async for block in response.aiter_bytes():
                        if len(data)+len(block)>24*1024*1024: raise ValueError("RPC response exceeds bound")
                        data.extend(block)
                value=parse_json(bytes(data))
                if "error" in value: raise ValueError(str(value["error"])[:200])
                self.cursor=(self.config["rpcs"].index(url)+1)%len(self.config["rpcs"])
                return value["result"]
            except (httpx.HTTPError,TimeoutError,ValueError) as error:
                errors.append(str(error)[:150])
        raise OSError("No configured consensus RPC answered: "+"; ".join(errors))

    async def state(self, endpoint=None):
        query=await self.rpc("abci_query",{"path":"/state","data":"","prove":False},endpoint)
        response=query["response"]
        if response["code"]!=0: raise ValueError("state query failed")
        state=parse_json(base64.b64decode(response["value"],validate=True))
        if state["chain_id"]!=self.plan["chain_id"] or state["plan_sha256"]!=self.config["plan_sha256"]:
            raise ValueError("RPC state differs from independently pinned chain/plan")
        return state

    async def submit(self, tx):
        wire=base64.b64encode(canonical_json(tx)).decode()
        start=time.monotonic(); last_send=0
        while time.monotonic()-start < 55:
            try:
                state=await self.state()
                actor=state["actors"][self.public]
                if actor["last_tx"]==tx["id"]:
                    with closing(self.connect()) as db, db: db.execute("DELETE FROM outbox WHERE id=1")
                    return actor["last_result"]
                if actor["sequence"]>=tx["body"]["sequence"] or state["height"]>tx["body"]["valid_until"]:
                    with closing(self.connect()) as db, db: db.execute("DELETE FROM outbox WHERE id=1")
                    raise ValueError("transaction superseded or expired")
                if time.monotonic()-last_send > 3:
                    response=await self.rpc("broadcast_tx_sync",{"tx":wire})
                    last_send=time.monotonic()
                    if response["code"]!=0:
                        # CheckTx rejection is definitive only for this attempt; the actor sequence is unchanged.
                        with closing(self.connect()) as db, db: db.execute("DELETE FROM outbox WHERE id=1")
                        raise ValueError("transaction rejected by validator: "+response.get("log",""))
            except (OSError,httpx.HTTPError):
                pass
            except ValueError as error:
                # Mempool duplicate errors are expected after an uncertain response; keep querying.
                if "already exists in cache" not in str(error) and "tx already exists" not in str(error): raise
            await asyncio.sleep(.4)
        raise OSError("commit not observed; signed transaction retained in durable outbox")

    async def command(self, kind, payload):
        async with self.serial:
            with closing(self.connect()) as db:
                pending=db.execute("SELECT payload FROM outbox WHERE id=1").fetchone()
            if pending:
                try: await self.submit(parse_json(pending[0]))
                except ValueError: pass
            state=await self.state()
            tx=sign({"schema":"dawl.cluster-tx/1","chain_id":state["chain_id"],"plan_sha256":state["plan_sha256"],
                     "sequence":state["actors"][self.public]["sequence"]+1,"valid_until":state["height"]+120,
                     "kind":kind,"payload":payload},self.key)
            with closing(self.connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO outbox VALUES (1,?)",(canonical_json(tx),))
            return await self.submit(tx)

    async def close(self):
        await self.http.aclose()
