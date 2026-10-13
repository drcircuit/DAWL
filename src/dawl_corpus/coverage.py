"""Exhaustive audit coverage across requests capped at 256 chunks each."""
import secrets
from time import perf_counter

from dawl_node.audit import verify_response
from dawl_node.store import validate_manifest


def audit_all(peer, snapshot_id, registry, tokenizer, public, batch_size=128):
    if type(batch_size) is not int or not 1 <= batch_size <= 256:
        raise ValueError("batch size must be 1..256")
    envelope = peer.manifest(snapshot_id)
    if envelope["id"] != snapshot_id:
        raise ValueError("unexpected snapshot")
    body = validate_manifest(envelope, registry, tokenizer, public)
    total = body["chunk_count"]
    attempted, verified, batches = set(), set(), []
    started = perf_counter()
    for first in range(0, total, batch_size):
        positions = list(range(first, min(first + batch_size, total)))
        if attempted.intersection(positions):
            raise ValueError("duplicate coverage")
        challenge = {"snapshot_id": snapshot_id, "nonce": secrets.token_hex(32), "positions": positions}
        response, size = peer.challenge(snapshot_id, challenge)
        result = verify_response(envelope, registry, tokenizer, public, challenge, response)
        result.pop("single_bad_chunk_detection_probability")
        result["scope"] = "Deterministic consecutive coverage batch; not a random spot-check."
        result["response_bytes"] = size
        batches.append(result)
        attempted.update(positions)
        if result["accepted"] and result["checked_chunks"] == len(positions):
            verified.update(positions)
    complete = attempted == verified == set(range(total))
    return {"schema": "dawl.corpus-coverage/1", "snapshot_id": snapshot_id, "source_registry": registry["id"],
            "total_chunks": total, "attempted_chunks": len(attempted), "verified_chunks": len(verified),
            "accepted": complete, "batch_size": batch_size, "batch_count": len(batches), "batches": batches,
            "elapsed_seconds": perf_counter() - started,
            "scope": "Every registered chunk re-tokenized against pinned inputs; no training or semantic-quality claim."}
