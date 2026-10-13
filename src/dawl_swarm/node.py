"""Independent discovery/gossip and CPU audit loops, with restart-safe challenges."""
import asyncio
import secrets
import time

import httpx

from dawl_node.common import canonical_json, load_key, read_json, sha256, sign
from dawl_platform.auditor import verify_book
from dawl_platform.workflow import open_node
from .protocol import (AUDITOR, LEDGER_REPLICATOR, PROVIDER, announcement, role_names,
                       validate_policy, verify_admission)
from .state import State
from .transfer import Puller, Unavailable, descriptor


def lineage_files(ledger, target=None):
    entries, seen, files = ([target] if target else list(ledger.entries())), set(), {}
    while entries:
        entry = entries.pop()
        if entry["id"] in seen:
            continue
        if len(seen) >= 100000:
            raise ValueError("lineage traversal cap reached")
        seen.add(entry["id"])
        for value in entry["body"]["payload"].get("files", {}).values():
            descriptor(value)
            previous = files.get(value["sha256"])
            if previous and previous != value:
                raise ValueError("conflicting descriptor sizes")
            files[value["sha256"]] = value
        if target:
            entries.extend(ledger.get(ref) for ref in entry["body"]["parents"])
    return files


class Node:
    def __init__(self, root, key_path=None):
        self.root = root
        self.config = read_json(root / "swarm.json")
        self.policy = read_json(root / "swarm-policy.json")
        self.pin = validate_policy(self.policy)
        if self.pin != self.config["policy_sha256"]:
            raise ValueError("swarm policy differs from independent pin")
        self.key = load_key(key_path or root / "key.private.pem")
        self.public = self.key.public_key().public_bytes_raw().hex()
        self.member = self.policy["members"][self.public]
        self.roles = self.member["roles"]
        self.cert = read_json(root / "admission.json")
        if verify_admission(self.cert, self.policy) != self.public:
            raise ValueError("admission belongs to a different key")
        self.ledger, self.objects = open_node(root)
        if self.ledger.policy != self.policy["lineage_policy"]:
            raise ValueError("lineage policy differs from swarm pin")
        self.ledger.verify_history()
        self.state = State(root / "swarm.sqlite", self.policy, self.ledger)
        self.schedule = self.state.schedule()
        self.known = set(self.config["bootnodes"]) | {e["body"]["signer"] for e in self.state.peers()}
        if not self.known <= self.policy["members"].keys():
            raise ValueError("unadmitted bootnode")
        if type(self.config["samples"]) is not int or not 1 <= self.config["samples"] <= 256:
            raise ValueError("invalid audit sample count")
        self.connections, self.tables, self.catalogs = {}, {}, {}
        self.active, self.phase = None, "starting"
        self.client, self.puller = None, None
        self.latest_announcement = None
        self.fault = {"delay_ms": 0, "corrupt": False, "withhold": False, "drop_every": 0,
                      "partition": False, "freeloader": False}
        self.served = 0
        self.files = lineage_files(self.ledger)

    def read_fault(self):
        # No network control endpoint. Only an explicitly enabled lab's local operator can change this file.
        if self.config.get("lab_faults") and (self.root / "fault.json").exists():
            versions = sorted((self.root / "faults").glob("*.json"))
            data = read_json(versions[-1] if versions else self.root / "fault.json")
            if set(data) != set(self.fault):
                raise ValueError("invalid local lab fault configuration")
            if (type(data["delay_ms"]) is not int or not 0 <= data["delay_ms"] <= 5000
                    or type(data["drop_every"]) is not int or not 0 <= data["drop_every"] <= 1000
                    or any(type(data[k]) is not bool for k in ("corrupt", "withhold", "partition", "freeloader"))):
                raise ValueError("invalid fault values")
            self.fault = data
        return self.fault

    def announce(self):
        self.files = lineage_files(self.ledger)
        inventory = sorted(d for d in self.files if (self.objects.root / d).is_file())[:512]
        self.latest_announcement = announcement(self.key, self.cert, self.policy, self.state.increment("announcement_sequence"),
                                                self.ledger.checkpoint(), inventory)
        return self.latest_announcement

    async def start(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(3), limits=httpx.Limits(max_connections=8),
                                        follow_redirects=False, trust_env=False)
        self.puller = Puller(self.client, self.objects, self.state,
                             [member["endpoint"] for member in self.policy["members"].values()])
        self.announce()
        self.tasks = [asyncio.create_task(self.control_loop()), asyncio.create_task(self.audit_loop())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()

    async def sync_ledger(self, public, checkpoint):
        endpoint = self.policy["members"][public]["endpoint"]
        cursor, ids = 0, set()
        for _ in range(1564):
            page = await self.puller.json(endpoint, f"/api/ledger?after={cursor}")
            if page.get("after") != cursor or not isinstance(page.get("entries"), list) or len(page["entries"]) > 64:
                raise ValueError("invalid ledger pagination")
            if not page["entries"]:
                if len(ids) != checkpoint["entries"] or sha256(canonical_json(sorted(ids))) != checkpoint["set_sha256"]:
                    raise ValueError("ledger export changed or is incomplete; retry next cycle")
                return
            if type(page.get("next")) is not int or page["next"] <= cursor:
                raise ValueError("nonadvancing ledger cursor")
            for envelope in page["entries"]:
                if envelope["id"] in ids:
                    raise ValueError("repeated ledger entry")
                ids.add(envelope["id"])
                self.ledger.add(envelope)
            cursor = page["next"]
        raise ValueError("ledger export exceeds cap")

    async def contact(self, public):
        member = self.policy["members"][public]
        try:
            await self.puller.request(member["endpoint"], "/swarm/announce", limit=1024,
                                      payload=canonical_json(self.latest_announcement))
            envelope = await self.puller.json(member["endpoint"], "/swarm/hello")
            if envelope["body"]["signer"] != public:
                raise ValueError("endpoint returned the wrong identity")
            await asyncio.to_thread(self.state.peer, envelope)
            peer_list = await self.puller.json(member["endpoint"], "/swarm/peers")
            if not isinstance(peer_list, list) or len(peer_list) > 64:
                raise ValueError("peer gossip exceeds cap")
            for peer in peer_list:
                try:
                    await asyncio.to_thread(self.state.peer, peer)
                    self.known.add(peer["body"]["signer"])
                except (ValueError, KeyError, TypeError):
                    self.state.increment("rejected_announcements")
            cp = envelope["body"]["checkpoint"]
            if member["roles"] & LEDGER_REPLICATOR and cp != self.ledger.checkpoint():
                await self.sync_ledger(public, cp)
            stream, cursor = self.state.cursor(public)
            for _ in range(3126):
                page = await self.puller.json(member["endpoint"], f"/swarm/observations?after={cursor}")
                if page.get("after") != cursor or not isinstance(page.get("entries"), list) or len(page["entries"]) > 32:
                    raise ValueError("invalid observation page")
                if stream is not None and page.get("stream") != stream:
                    stream, cursor = None, 0
                    continue  # Recreated peer stores require a fresh export, not a skipped prefix.
                stream = page["stream"]
                if not page["entries"]:
                    break
                if type(page.get("next")) is not int or page["next"] <= cursor:
                    raise ValueError("nonadvancing observation cursor")
                for entry in page["entries"]:
                    await asyncio.to_thread(self.state.add, entry)  # Never imports a sender's 'locally_executed' flag.
                cursor = page["next"]
                self.state.advance(public, stream, cursor)
            else:
                raise ValueError("observation export exceeds cap")
            self.connections[public] = {"reachable": True, "last_contact": time.time(), "error": ""}
        except (httpx.HTTPError, TimeoutError, ValueError, OSError, KeyError, TypeError) as error:
            self.connections[public] = {"reachable": False, "last_contact": time.time(), "error": str(error)[:200]}
            self.state.increment("peer_contact_failures")

    async def control_loop(self):
        while True:
            try:
                self.read_fault()
                if not self.fault["partition"]:
                    await asyncio.gather(*(self.contact(p) for p in sorted(self.known - {self.public})))
                    if self.roles & LEDGER_REPLICATOR:
                        await asyncio.to_thread(self.state.batch, self.key)
                await asyncio.to_thread(self.announce)
            except Exception as error:
                self.state.event("control_error", error=str(error)[:300])
            await asyncio.sleep(3)

    def providers(self):
        # Use independent admitted alternatives even if a malicious peer omits them from its inventory.
        return [self.policy["members"][p]["endpoint"] for p in sorted(self.known - {self.public},
                key=lambda p: self.policy["members"][p]["name"])
                if self.policy["members"][p]["roles"] & PROVIDER]

    def challenge(self, entry, recheck=None):
        total = entry["body"]["payload"]["chunks"]
        if type(total) is not int or not 1 <= total <= 100000:
            raise ValueError("invalid committed source coverage")
        return {"vectors_entry": entry["id"], "checkpoint": self.ledger.checkpoint(), "total_chunks": total,
                "nonce": secrets.token_hex(32), "rechecks": recheck["id"] if recheck else None,
                "positions": recheck["body"]["positions"] if recheck else sorted(secrets.SystemRandom().sample(
                    range(total), min(total, self.config["samples"]))) }

    async def execute(self, identity, entry, challenge):
        retained = self.state.job(identity, challenge)
        if retained["receipt"] or retained["retry_at"] > time.time():
            return
        challenge = retained["challenge"]
        self.active, self.phase = entry["id"], "pulling"
        started, cpu_ms, error, outcome = time.perf_counter(), 0.0, "", "error"
        try:
            if self.fault["partition"]:
                raise Unavailable("local node is partitioned in the lab")
            if self.fault["freeloader"]:
                outcome = "pass"  # Deliberately dishonest behavior, enabled only in the lab's local config.
            else:
                files = lineage_files(self.ledger, entry)
                for value in files.values():
                    await self.puller.fetch(value, self.providers())
                self.phase = "cpu-replay"
                begin = time.perf_counter()
                try:
                    await asyncio.to_thread(verify_book, self.ledger, self.objects, None, entry, self.tables, challenge["positions"])
                    outcome = "pass"
                finally:
                    cpu_ms = (time.perf_counter() - begin) * 1000
        except Unavailable as failure:
            outcome, error = "unavailable", str(failure)
        except ValueError as failure:
            outcome, error = "invalid", str(failure)
        except (OSError, KeyError, TypeError, IndexError, RuntimeError) as failure:
            outcome, error = "error", str(failure)
        receipt = sign({"schema": "dawl.swarm-audit/1", "policy": self.pin, **challenge, "outcome": outcome,
                        "vector_commitment_match": {"pass": True, "invalid": False}.get(outcome),
                        "execution_time_ms": round(cpu_ms, 3), "elapsed_time_ms": round((time.perf_counter() - started) * 1000, 3),
                        "error": error[:1024], "scope": "sampled-input-embedding/v1"}, self.key)
        self.state.add(receipt, local=not self.fault["freeloader"])
        self.state.finish(identity, receipt)
        self.schedule[identity] = (outcome in ("pass", "invalid"), time.time() + 30)
        self.state.event("audit_finished", receipt=receipt["id"], vectors_entry=entry["id"], outcome=outcome, rechecks=challenge["rechecks"])
        self.active, self.phase = None, "idle"

    async def audit_loop(self):
        self.phase = "idle"
        while True:
            try:
                if self.roles & AUDITOR:
                    # Replays are serialized: one CPU job and one table cache per auditor.
                    for entry in list(self.ledger.entries()):
                        if entry["body"]["kind"] == "vectors" and self.due(entry["id"]):
                            await self.execute(entry["id"], entry, self.challenge(entry))
                    if not self.fault["freeloader"]:
                        for row in self.state.receipts():
                            receipt = row["envelope"]
                            body = receipt["body"]
                            # Recheck original claims once; never recursively audit rechecks.
                            if body["signer"] != self.public and body["rechecks"] is None and body["outcome"] in ("pass", "invalid") and self.due(receipt["id"]):
                                entry = self.ledger.get(body["vectors_entry"])
                                await self.execute(receipt["id"], entry, self.challenge(entry, receipt))
            except Exception as error:
                self.active, self.phase = None, "error"
                self.state.event("worker_error", error=str(error)[:300])
            await asyncio.sleep(2)

    def due(self, identity):
        complete, retry_at = self.schedule.get(identity, (False, 0))
        return not complete and retry_at <= time.time()

    def status(self):
        receipts = self.state.receipts()
        by_id = {r["envelope"]["id"]: r for r in receipts}
        disputes = []
        for row in receipts:
            b = row["envelope"]["body"]
            prior = by_id.get(b["rechecks"])
            if (row["locally_executed"] and prior and b["outcome"] in ("pass", "invalid")
                    and b["vectors_entry"] == prior["envelope"]["body"]["vectors_entry"]
                    and b["positions"] == prior["envelope"]["body"]["positions"]
                    and b["outcome"] != prior["envelope"]["body"]["outcome"]):
                disputes.append({"claim": prior["envelope"]["id"], "replay": row["envelope"]["id"]})
        return {"name": self.member["name"], "public": self.public, "roles": role_names(self.roles), "policy": self.pin,
                "checkpoint": self.ledger.checkpoint(), "phase": self.phase, "active": self.active,
                "peers": [{"public": p, "name": m["name"], **self.connections.get(p, {"reachable": False})}
                          for p, m in self.policy["members"].items() if p != self.public and p in self.known],
                "announcement": self.latest_announcement, "counters": self.state.counters(), "events": self.state.events(),
                "receipts": receipts[-100:], "receipt_count": len(receipts),
                "receipt_set_sha256": sha256(canonical_json(sorted(by_id))), "disputes": disputes,
                "scope": "Signed observations; no proof of work, reward eligibility, global consensus or model training."}
