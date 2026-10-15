"""Separate admission and observation protocol; never grants dataset signing rights."""
import time
from urllib.parse import urlsplit

from dawl_node.common import canonical_json, sha256, sign, verify_signature
from data_ingestion.protocol import require_digest

PROVIDER, AUDITOR, LEDGER_REPLICATOR = 1, 2, 4
ROLES = {PROVIDER: "PROVIDER", AUDITOR: "AUDITOR", LEDGER_REPLICATOR: "LEDGER_REPLICATOR"}
MAX_MESSAGE = 64 * 1024


def role_names(mask):
    return [name for bit, name in ROLES.items() if mask & bit]


def origin(value):
    parsed = urlsplit(value)
    # Static numeric lab addresses or independently configured HTTPS origins only.
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or parsed.port is None):
        raise ValueError("admission endpoint must be an exact origin with an explicit port")
    return value


def validate_policy(policy):
    if set(policy) != {"schema", "authority", "lineage_policy", "members"} or policy["schema"] != "dawl.swarm-policy/1":
        raise ValueError("invalid swarm policy")
    require_digest(policy["authority"])
    require_digest(policy["lineage_policy"])
    if not isinstance(policy["members"], dict) or not 1 <= len(policy["members"]) <= 64:
        raise ValueError("swarm admits 1..64 configured members")
    endpoints = set()
    for public, member in policy["members"].items():
        require_digest(public)
        if (set(member) != {"name", "roles", "endpoint"} or not isinstance(member["name"], str)
                or not 1 <= len(member["name"]) <= 64 or type(member["roles"]) is not int or not 1 <= member["roles"] <= 7):
            raise ValueError("invalid member")
        endpoint = origin(member["endpoint"])
        if endpoint in endpoints:
            raise ValueError("duplicate endpoint")
        endpoints.add(endpoint)
    return sha256(canonical_json(policy))


def admit(policy, public, authority):
    return sign({"schema": "dawl.swarm-admission/1", "policy": validate_policy(policy),
                 "public": public, **policy["members"][public]}, authority)


def verify_admission(cert, policy):
    body = verify_signature(cert, policy["authority"])
    public = body["public"]
    if public not in policy["members"] or body != {"schema": "dawl.swarm-admission/1",
            "policy": validate_policy(policy), "public": public, **policy["members"][public], "signer": policy["authority"]}:
        raise ValueError("admission does not match independently pinned membership")
    return public


def announcement(key, cert, policy, sequence, checkpoint, inventory):
    return sign({"schema": "dawl.swarm-announcement/1", "policy": validate_policy(policy), "certificate": cert,
                 "sequence": sequence, "issued_at": int(time.time()), "expires_at": int(time.time()) + 120,
                 "checkpoint": checkpoint, "inventory": inventory}, key)


def verify_announcement(envelope, policy, now=None):
    if len(canonical_json(envelope)) > MAX_MESSAGE:
        raise ValueError("announcement exceeds cap")
    public = verify_admission(envelope["body"]["certificate"], policy)
    body = verify_signature(envelope, public)
    if set(body) != {"schema", "policy", "certificate", "sequence", "issued_at", "expires_at", "checkpoint", "inventory", "signer"}:
        raise ValueError("invalid announcement fields")
    now = int(time.time()) if now is None else now
    if (body["schema"] != "dawl.swarm-announcement/1" or body["policy"] != validate_policy(policy)
            or type(body["sequence"]) is not int or body["sequence"] < 1
            or type(body["issued_at"]) is not int or type(body["expires_at"]) is not int
            or not now - 120 <= body["issued_at"] <= now + 15
            or not now < body["expires_at"] <= body["issued_at"] + 120):
        raise ValueError("stale or invalid announcement")
    cp = body["checkpoint"]
    if (set(cp) != {"policy", "entries", "set_sha256"} or cp["policy"] != policy["lineage_policy"]
            or type(cp["entries"]) is not int or not 0 <= cp["entries"] <= 100000):
        raise ValueError("invalid peer checkpoint")
    require_digest(cp["set_sha256"])
    if (not isinstance(body["inventory"], list) or len(body["inventory"]) > 512
            or len(set(body["inventory"])) != len(body["inventory"])):
        raise ValueError("invalid inventory")
    for digest in body["inventory"]:
        require_digest(digest)
    return body


def validate_receipt(envelope, policy, ledger):
    if len(canonical_json(envelope)) > MAX_MESSAGE:
        raise ValueError("receipt exceeds cap")
    public = envelope["body"]["signer"]
    if not policy["members"].get(public, {}).get("roles", 0) & AUDITOR:
        raise ValueError("receipt signer lacks auditor admission")
    body = verify_signature(envelope, public)
    required = {"schema", "policy", "signer", "vectors_entry", "checkpoint", "nonce", "positions", "total_chunks",
                "outcome", "vector_commitment_match", "execution_time_ms", "elapsed_time_ms", "error", "rechecks", "scope"}
    if set(body) != required or body["schema"] != "dawl.swarm-audit/1" or body["policy"] != validate_policy(policy):
        raise ValueError("invalid receipt fields or policy")
    require_digest(body["nonce"])
    cp = body["checkpoint"]
    if (set(cp) != {"policy", "entries", "set_sha256"} or cp["policy"] != ledger.policy
            or type(cp["entries"]) is not int or not 1 <= cp["entries"] <= 100000):
        raise ValueError("invalid receipt checkpoint")
    require_digest(cp["set_sha256"])
    target = ledger.get(body["vectors_entry"])
    if target["body"]["kind"] != "vectors" or body["total_chunks"] != target["body"]["payload"]["chunks"]:
        raise ValueError("unknown vector target or coverage")
    positions = body["positions"]
    if (not isinstance(positions, list) or not 1 <= len(positions) <= min(256, body["total_chunks"])
            or any(type(i) is not int or not 0 <= i < body["total_chunks"] for i in positions)
            or positions != sorted(set(positions))):
        raise ValueError("invalid sample positions")
    if body["outcome"] not in ("pass", "invalid", "unavailable", "error"):
        raise ValueError("invalid audit outcome")
    if body["vector_commitment_match"] is not ({"pass": True, "invalid": False}.get(body["outcome"])):
        raise ValueError("outcome and commitment result disagree")
    for field in ("execution_time_ms", "elapsed_time_ms"):
        if type(body[field]) not in (int, float) or not 0 <= body[field] <= 86400000:
            raise ValueError("invalid timing claim")
    if not isinstance(body["error"], str) or len(body["error"]) > 1024 or body["scope"] != "sampled-input-embedding/v1":
        raise ValueError("invalid receipt scope or error")
    if body["rechecks"] is not None:
        require_digest(body["rechecks"])
    return body
