"""Pure deterministic transitions. No clock, network, random draw or artifact I/O.

Consensus orders claims; separate admitted workers attest to computation. Neither
a quorum certificate nor two matching worker claims is a cryptographic proof of work.
"""
import copy

from dawl_node.common import canonical_json, sha256, verify_signature
from data_ingestion.protocol import require_digest
from .incidents import observe, worker_conflict

MAX_TX = 65536
AUDIT = "AUDIT_CPU"
PUBLISH = "PUBLISHER"


def initial(plan):
    if plan.get("schema") != "dawl.cluster-plan/1":
        raise ValueError("unsupported cluster plan")
    if not 1 <= len(plan["jobs"]) <= 10000 or not 1 <= len(plan["actors"]) <= 64:
        raise ValueError("plan exceeds operational bounds")
    if not 2 <= plan["replicas"] <= 4 or not 10 <= plan["lease_blocks"] <= 600:
        raise ValueError("invalid replication or lease policy")
    jobs = {}
    coverage = {b["vectors_entry"]: set() for b in plan["books"]}
    for job in plan["jobs"]:
        spec = job["spec"]
        if job["id"] != sha256(canonical_json(spec)) or job["id"] in jobs:
            raise ValueError("duplicate or altered job definition")
        positions = spec["positions"]
        book = next(b for b in plan["books"] if b["vectors_entry"] == spec["vectors_entry"])
        if (not positions or len(positions) > 256 or positions != sorted(set(positions))
                or any(type(x) is not int or not 0 <= x < book["chunks"] for x in positions)
                or coverage[spec["vectors_entry"]].intersection(positions)):
            raise ValueError("overlapping or invalid job coverage")
        coverage[spec["vectors_entry"]].update(positions)
        jobs[job["id"]] = {"spec": spec, "leases": {}, "attestations": {}, "failures": [], "attempts": 0}
    for book in plan["books"]:
        if coverage[book["vectors_entry"]] != set(range(book["chunks"])):
            raise ValueError("incomplete planned corpus coverage")
    for public, actor in plan["actors"].items():
        require_digest(public)
        if not set(actor["hats"]) <= {AUDIT, PUBLISH, "TRAINER_GPU"}:
            raise ValueError("unrecognized actor capability")
    if sum(AUDIT in a["hats"] for a in plan["actors"].values()) < plan["replicas"]:
        raise ValueError("insufficient distinct admitted auditors")
    state = {"schema": "dawl.cluster-state/1", "chain_id": plan["chain_id"],
            "plan_sha256": sha256(canonical_json(plan)), "plan": plan, "height": 0,
            "jobs": jobs, "actors": {p: {"sequence": 0, "last_tx": None, "last_result": None}
                                      for p in plan["actors"]},
            "dataset_release": None, "training_plans": {},
            "weights": {plan["imported_weight"]["id"]: plan["imported_weight"]},
            "expired_leases": 0, "rejected_transactions": 0}
    if plan.get("evidence_contract") == "dawl.incidents/1":
        state["incidents"]={"recent":[],"counts":{},"worker_conflicts":{}}
    elif "evidence_contract" in plan:
        raise ValueError("unknown incident contract")
    return state


def advance(state, height):
    if type(height) is not int or height != state["height"] + 1:
        raise ValueError("nonconsecutive block height")
    state["height"] = height
    for identity,job in sorted(state["jobs"].items()):
        for owner, lease in sorted(job["leases"].items()):
            if lease["expires"] < height:
                observe(state,"lease_expired",{"job":identity,"signer":owner,**lease,"misconduct_proven":False})
                del job["leases"][owner]
                state["expired_leases"] += 1


def complete(state, job):
    return len(job["attestations"]) >= state["plan"]["replicas"] and not any(
        f["outcome"] == "invalid" for f in job["failures"])


def apply(state, tx):
    if len(canonical_json(tx)) > MAX_TX:
        raise ValueError("transaction exceeds byte limit")
    body = tx["body"]
    public = body["signer"]
    actor = state["actors"].get(public)
    if actor is None:
        raise ValueError("unadmitted transaction signer")
    body = verify_signature(tx, public)
    if set(body) != {"schema", "chain_id", "plan_sha256", "signer", "sequence", "valid_until", "kind", "payload"}:
        raise ValueError("invalid transaction fields")
    if (body["schema"] != "dawl.cluster-tx/1" or body["chain_id"] != state["chain_id"]
            or body["plan_sha256"] != state["plan_sha256"]):
        raise ValueError("wrong chain or plan pin")
    if type(body["sequence"]) is not int or body["sequence"] != actor["sequence"] + 1:
        raise ValueError("replayed or out-of-order actor sequence")
    height = state["height"]
    if type(body["valid_until"]) is not int or not height <= body["valid_until"] <= height + 600:
        raise ValueError("transaction expired or validity window exceeds bound")
    kind, payload = body["kind"], body["payload"]
    hats = state["plan"]["actors"][public]["hats"]
    result = {}
    if kind == "claim":
        if AUDIT not in hats or payload != {"capability": AUDIT}:
            raise ValueError("signer cannot claim this capability")
        for identity, job in sorted(state["jobs"].items()):
            if public in job["leases"]:
                result = {"job": identity, **job["leases"][public]}
                break
        else:
            # Deterministic balancing. Different workers consume capacity in consensus order.
            candidates = [(len(j["attestations"])+len(j["leases"]), i, j) for i,j in state["jobs"].items()
                          if not complete(state,j) and public not in j["attestations"]
                          and len(j["attestations"])+len(j["leases"]) < state["plan"]["replicas"]
                          and not any(f["outcome"] == "invalid" for f in j["failures"])]
            if candidates:
                _, identity, job = min(candidates, key=lambda r:(r[0],r[1]))
                lease = {"generation": tx["id"], "expires": height+state["plan"]["lease_blocks"]}
                job["leases"][public] = lease
                job["attempts"] += 1
                result = {"job": identity, **lease}
            else:
                result = {"job": None}
    elif kind in ("renew", "result"):
        if AUDIT not in hats:
            raise ValueError("auditor capability required")
        require_digest(payload["job"])
        job = state["jobs"][payload["job"]]
        lease = job["leases"].get(public)
        if not lease or lease["generation"] != payload["generation"] or lease["expires"] < height:
            raise ValueError("stale lease generation or wrong owner")
        if kind == "renew":
            if set(payload) != {"job", "generation"}:
                raise ValueError("invalid renewal")
            lease["expires"] = height+state["plan"]["lease_blocks"]
            result = {"job": payload["job"], **lease}
        else:
            if set(payload) != {"job", "generation", "report"}:
                raise ValueError("invalid completion")
            report = payload["report"]
            if set(report) != {"outcome", "checked_chunks", "tokens_checked", "dense_bytes", "implementation_sha256", "execution_ms", "error"}:
                raise ValueError("invalid result fields")
            require_digest(report["implementation_sha256"])
            if report["implementation_sha256"] != state["plan"]["implementation_sha256"]:
                raise ValueError("worker implementation differs from admitted execution recipe")
            if (report["outcome"] not in ("pass", "invalid", "unavailable", "error")
                    or not isinstance(report["error"], str) or len(report["error"]) > 512
                    or any(type(report[f]) is not int or not 0 <= report[f] <= 2**50
                           for f in ("checked_chunks", "tokens_checked", "dense_bytes", "execution_ms"))):
                raise ValueError("invalid result values")
            if report["outcome"] == "pass":
                spec = job["spec"]
                if (report["checked_chunks"] != len(spec["positions"])
                        or report["tokens_checked"] != spec["tokens"]
                        or report["dense_bytes"] != spec["dense_bytes"] or report["error"]):
                    raise ValueError("completion does not match pinned job coverage")
                job["attestations"][public] = {"transaction": tx["id"], "height": height, "report": report}
            else:
                # Keep bounded diagnostic history; invalid marks the shard disputed.
                job["failures"] = (job["failures"]+[dict(report, signer=public, height=height, transaction=tx["id"])])[-64:]
                observe(state,"worker_reported_"+report["outcome"],{"job":payload["job"],"signer":public,
                        "transaction":tx["id"],"error":report["error"],"misconduct_proven":False})
            del job["leases"][public]
            result = {"job": payload["job"], "complete": complete(state,job)}
    elif kind == "record_worker_conflict":
        if PUBLISH not in hats:
            raise ValueError("publisher capability required to submit conflict evidence")
        result=worker_conflict(state,payload)
    elif kind == "publish_dataset":
        if PUBLISH not in hats or payload != {}:
            raise ValueError("publisher capability required")
        if not all(complete(state,j) for j in state["jobs"].values()):
            raise ValueError("dataset requires complete independent shard coverage")
        if state["dataset_release"] is None:
            release = {"schema": "dawl.dataset-release/1", "plan_sha256": state["plan_sha256"],
                       "lineage_checkpoint": state["plan"]["lineage_checkpoint"],
                       "training_manifest": state["plan"]["training_manifest"],
                       "books": state["plan"]["books"], "replicas": state["plan"]["replicas"],
                       "receipts": sorted(a["transaction"] for j in state["jobs"].values() for a in j["attestations"].values()),
                       "scope": "Full planned chunk coverage attested by distinct admitted auditor keys; not proof of work."}
            state["dataset_release"] = {"id": sha256(canonical_json(release)), "body": release, "height": height}
        result = {"dataset_release": state["dataset_release"]["id"]}
    elif kind == "plan_training":
        if PUBLISH not in hats:
            raise ValueError("publisher capability required")
        if set(payload) != {"dataset_release", "parent_weight", "recipe"}:
            raise ValueError("invalid training-plan fields")
        if not state["dataset_release"] or payload["dataset_release"] != state["dataset_release"]["id"]:
            raise ValueError("training must pin a released dataset")
        if payload["parent_weight"] not in state["weights"]:
            raise ValueError("training must pin registered parent weights")
        recipe = payload["recipe"]
        if (set(recipe) != {"executor", "sequence_length", "seed", "requires", "optimizer", "learning_rate"}
                or recipe["executor"] != "causal-lm-next-token/v1" or recipe["requires"] != "TRAINER_GPU"
                or type(recipe["sequence_length"]) is not int or not 16 <= recipe["sequence_length"] <= 1024
                or type(recipe["seed"]) is not int or not 0 <= recipe["seed"] < 2**32
                or recipe["optimizer"] != "adamw" or recipe["learning_rate"] != "0.0003"):
            raise ValueError("unsupported pinned training recipe")
        record = {"schema": "dawl.training-plan/1", **payload, "status": "planned", "executed": False}
        identity = sha256(canonical_json(record))
        state["training_plans"][identity] = record
        result = {"training_plan": identity}
    else:
        raise ValueError("unsupported command; training and weight promotion are not enabled")
    actor.update(sequence=body["sequence"], last_tx=tx["id"], last_result=result)
    return result


def evaluate(state, tx):
    """Transaction atomicity: any rejection discards all tentative mutations."""
    candidate = copy.deepcopy(state)
    try:
        result = apply(candidate, tx)
        return candidate, {"code": 0, "log": "", "data": result}
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OverflowError, RecursionError):
        return state, {"code": 1, "log": "transaction rejected by deterministic DAWL policy"}


def summary(state):
    jobs = list(state["jobs"].values())
    return {"height": state["height"], "chain_id": state["chain_id"], "plan_sha256": state["plan_sha256"],
            "app_hash": sha256(canonical_json(state)), "jobs": len(jobs),
            "complete": sum(complete(state,j) for j in jobs),
            "leases": sum(len(j["leases"]) for j in jobs), "attempts": sum(j["attempts"] for j in jobs),
            "attestations": sum(len(j["attestations"]) for j in jobs),
            "verified_chunk_executions": sum(len(j["spec"]["positions"])*len(j["attestations"]) for j in jobs),
            "corpus_chunks": sum(b["chunks"] for b in state["plan"]["books"]),
            "corpus_tokens": sum(b["tokens"] for b in state["plan"]["books"]),
            "disputed": sum(any(f["outcome"] == "invalid" for f in j["failures"]) for j in jobs),
            "expired_leases": state["expired_leases"], "rejected_transactions": state["rejected_transactions"],
            "dataset_release": state["dataset_release"], "training_plans": state["training_plans"],
            "weights": state["weights"],
            "incidents": state.get("incidents"),
            "workers": {p:{"name":state["plan"]["actors"][p]["name"], **v} for p,v in state["actors"].items()}}
