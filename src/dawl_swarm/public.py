"""Replay a gossiped claim with public policy pins and no admitted private key."""
import asyncio

import httpx

from dawl_node.common import canonical_json, read_json, sha256, write_json
from dawl_platform.artifacts import PublicPeer, sync
from dawl_platform.auditor import verify_book
from dawl_platform.workflow import open_node
from .node import lineage_files
from .protocol import validate_policy, validate_receipt
from .state import State
from .transfer import Puller, Unavailable


async def replay(root, peer_url, policy_path, policy_pin, trust_path, trust_pin, receipt_id):
    policy, trust = read_json(policy_path), read_json(trust_path)
    if validate_policy(policy) != policy_pin or sha256(canonical_json(trust)) != trust_pin or policy["lineage_policy"] != trust_pin:
        raise ValueError("public policy or lineage trust differs from independent pins")
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "trust.json", trust)
    ledger, objects = open_node(root)
    peer = PublicPeer(peer_url)  # Public HTTPS or a local forwarded lab port; never implicitly allows remote plaintext.
    await asyncio.to_thread(sync, ledger, peer)
    receipt = await asyncio.to_thread(peer.json, "/swarm/observations/" + receipt_id)
    if receipt["id"] != receipt_id:
        raise ValueError("different receipt returned")
    body = validate_receipt(receipt, policy, ledger)
    state = State(root / "swarm.sqlite", policy, ledger)
    state.add(receipt)
    target = ledger.get(body["vectors_entry"])
    outcome, error, result = "error", "", None
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
        puller = Puller(client, objects, state, [peer.url])
        try:
            for value in lineage_files(ledger, target).values():
                await puller.fetch(value, [peer.url])
            result = await asyncio.to_thread(verify_book, ledger, objects, None, target, {}, body["positions"])
            outcome = "pass"
        except Unavailable as failure:
            outcome, error = "unavailable", str(failure)
        except ValueError as failure:
            outcome, error = "invalid", str(failure)
    report = {"schema": "dawl.public-swarm-replay/1", "receipt": receipt, "private_keys_used": False,
              "checkpoint": ledger.verify_history(), "outcome": outcome, "error": error, "result": result,
              "matches_claim": outcome == body["outcome"] if outcome in ("pass", "invalid") else None,
              "counters": state.counters(), "scope": "Independent replay of the exact positions in a signed claim; not proof of the claimant's work."}
    write_json(root / "public-replay.json", report)
    return report
