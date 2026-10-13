"""Independent validator hat. A pass is evidence about sampled chunks only."""

from __future__ import annotations

import secrets
from time import perf_counter

from data_ingestion.protocol import canonical_json, sha256, verify_proof
from data_ingestion.validator_node import detection_probability
from .common import pack_ids, sign
from .store import validate_manifest


def make_challenge(envelope: dict, registry: dict, tokenizer, public: str, samples: int) -> dict:
    body = validate_manifest(envelope, registry, tokenizer, public)
    if type(samples) is not int or not 1 <= samples <= min(256, body["chunk_count"]):
        raise ValueError("sample count must be 1..min(256, source chunk count)")
    return {"snapshot_id": envelope["id"], "nonce": secrets.token_hex(32),
            "positions": sorted(secrets.SystemRandom().sample(range(body["chunk_count"]), samples))}


def verify_response(envelope: dict, registry: dict, tokenizer, public: str,
                    challenge: dict, response: dict) -> dict:
    start = perf_counter()
    body = validate_manifest(envelope, registry, tokenizer, public)
    failures = []
    checked = 0
    try:
        if set(challenge) != {"snapshot_id", "nonce", "positions"} or challenge["snapshot_id"] != envelope["id"]:
            raise ValueError("invalid retained challenge")
        positions = challenge["positions"]
        if (not positions or len(positions) > 256 or len(set(positions)) != len(positions)
                or any(type(index) is not int or not 0 <= index < body["chunk_count"] for index in positions)):
            raise ValueError("invalid challenge positions")
        if (set(response) != {"snapshot_id", "nonce", "samples"}
                or response["snapshot_id"] != challenge["snapshot_id"] or response["nonce"] != challenge["nonce"]):
            raise ValueError("response does not match the retained challenge")
        if not isinstance(response["samples"], list) or len(response["samples"]) != len(positions):
            raise ValueError("missing or extra sample evidence")
        seen = set()
        shard_map = {shard["name"]: shard for shard in body["shards"]}
        for evidence in response["samples"]:
            if set(evidence) != {"source", "raw_text", "source_proof", "shard", "record", "token_ids", "token_proof"}:
                raise ValueError("unexpected sample fields")
            source, record = evidence["source"], evidence["record"]
            index = source["index"]
            if type(index) is not int or index not in positions or index in seen:
                raise ValueError("duplicate or unsolicited sample position")
            seen.add(index)
            if source != registry["body"]["records"][index]:
                raise ValueError("source record differs from the independent commitment")
            raw = evidence["raw_text"].encode("utf-8", errors="strict")
            if len(raw) != source["byte_length"] or sha256(raw) != source["sha256"]:
                raise ValueError(f"source bytes mismatch at chunk {index}")
            if not verify_proof(b"source", index, canonical_json(source), tuple(evidence["source_proof"]),
                                registry["body"]["root"], body["chunk_count"]):
                failures.append(f"source proof mismatch at chunk {index}")
            shard = shard_map[evidence["shard"]]
            local_index = index - shard["first_chunk"]
            if set(record) != {"source_index", "token_offset", "token_count", "sha256"}:
                raise ValueError("invalid token record")
            if (type(record["source_index"]) is not int or record["source_index"] != index
                    or type(record["token_count"]) is not int or not 1 <= record["token_count"] <= 16384
                    or type(record["token_offset"]) is not int or record["token_offset"] < 0
                    or record["token_offset"] + record["token_count"] > shard["token_count"]):
                raise ValueError("invalid token record bounds")
            if not verify_proof(b"token", local_index, canonical_json(record), tuple(evidence["token_proof"]),
                                shard["root"], shard["chunk_count"]):
                failures.append(f"token proof mismatch at chunk {index}")
            ids = evidence["token_ids"]
            if not isinstance(ids, list) or len(ids) != record["token_count"]:
                raise ValueError("invalid token sample length")
            if sha256(pack_ids(ids)) != record["sha256"]:
                failures.append(f"token bytes mismatch at chunk {index}")
            if ids != tokenizer.encode(evidence["raw_text"]):
                failures.append(f"retokenization mismatch at chunk {index}")
            checked += 1
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as error:
        failures.append(f"invalid evidence: {error}")
    return {"schema": "dawl.audit-result/v2", "snapshot_id": envelope["id"],
            "source_registry": registry["id"], "accepted": not failures,
            "nonce": challenge.get("nonce"), "positions": challenge.get("positions"),
            "checked_chunks": checked, "total_chunks": body["chunk_count"],
            "single_bad_chunk_detection_probability": detection_probability(body["chunk_count"], 1, len(challenge["positions"])),
            "failures": failures, "verification_seconds": perf_counter() - start,
            "scope": "Source/token consistency of uniformly sampled chunks; not semantic quality or proof of work."}


def audit_peer(peer, snapshot_id: str, registry: dict, tokenizer, public: str, samples: int, key=None) -> dict:
    start = perf_counter()
    envelope = peer.manifest(snapshot_id)
    if envelope["id"] != snapshot_id:
        raise ValueError("peer returned a different snapshot")
    challenge = make_challenge(envelope, registry, tokenizer, public, samples)
    response, response_bytes = peer.challenge(snapshot_id, challenge)
    result = verify_response(envelope, registry, tokenizer, public, challenge, response)
    result.update({"response_bytes": response_bytes, "round_trip_seconds": perf_counter() - start})
    return sign(result, key) if key else result
