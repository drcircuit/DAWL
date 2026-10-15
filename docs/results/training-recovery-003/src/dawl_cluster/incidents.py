"""Bounded attributed incidents. Recording an incident never changes voting power."""
import base64
from dawl_node.common import canonical_json, sha256, verify_signature


def observe(state, kind, detail):
    if "incidents" not in state:
        return
    item={"kind":kind,"height":state["height"],"detail":detail}
    state["incidents"]["recent"]=(state["incidents"]["recent"]+[item])[-256:]
    counts=state["incidents"]["counts"]
    counts[kind]=counts.get(kind,0)+1


def consensus_reports(state, reports):
    if "incidents" not in state:
        return
    for report in reports or []:
        # Only the private ABCI channel supplies this engine-validated summary.
        if (report["type"] not in (1,2) or len(base64.b64decode(report["validator"]["address"],validate=True))!=20
                or type(report["height"]) is not int or not 0<report["height"]<state["height"]
                or not 0<report["validator"]["power"]<=report["total_voting_power"]
                or len(canonical_json(report))>2048):
            raise ValueError("invalid CometBFT misbehavior summary")
        observe(state,"consensus_misbehavior",report)


def worker_conflict(state, payload):
    if "incidents" not in state or set(payload)!={"results"} or len(payload["results"])!=2:
        raise ValueError("two signed results required")
    a,b=payload["results"]
    signer=a["body"]["signer"]
    aa,bb=verify_signature(a,signer),verify_signature(b,signer)
    for body in (aa,bb):
        if (body["schema"]!="dawl.cluster-tx/1" or body["chain_id"]!=state["chain_id"]
                or body["plan_sha256"]!=state["plan_sha256"] or body["kind"]!="result"):
            raise ValueError("conflict belongs to another protocol or chain")
    pa,pb=aa["payload"],bb["payload"]
    if pa["job"]!=pb["job"] or pa["generation"]!=pb["generation"]:
        raise ValueError("results concern different assignments")
    accepted=state["jobs"][pa["job"]]["attestations"].get(signer)
    if not accepted or accepted["transaction"] not in (a["id"],b["id"]):
        raise ValueError("one result must be the committed attestation for this job")
    # Only pass versus invalid is an objectively contradictory outcome here.
    # Transport errors, timings, repeated delivery or a different generation are not equivocation.
    if {pa["report"]["outcome"],pb["report"]["outcome"]}!={"pass","invalid"}:
        raise ValueError("no contradictory pass/invalid statements")
    pair=sorted((a,b),key=lambda item:item["id"])
    identity=sha256(canonical_json(pair))
    conflicts=state["incidents"]["worker_conflicts"]
    if identity not in conflicts:
        if len(conflicts)>=64:
            raise ValueError("conflict retention bound reached")
        conflicts[identity]={"results":pair,"recorded_height":state["height"],"punishment":None}
        job=state["jobs"][pa["job"]]
        job["failures"]=(job["failures"]+[{"outcome":"invalid","signer":signer,
                         "height":state["height"],"evidence":identity,"error":"conflicting signed results"}])[-64:]
        observe(state,"worker_conflicting_results",{"id":identity,"signer":signer,"job":pa["job"]})
    return {"evidence":identity,"punishment":None}
