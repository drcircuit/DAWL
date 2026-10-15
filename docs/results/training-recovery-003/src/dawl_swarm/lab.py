"""Create an isolated, explicitly malicious lab without altering the platform baseline."""
import copy
from pathlib import Path
import shutil

from dawl_node.common import canonical_json, keygen, load_key, read_json, sha256, write_json
from dawl_platform.workflow import open_node
from .node import lineage_files
from .protocol import AUDITOR, LEDGER_REPLICATOR, PROVIDER, admit, validate_policy

NODES = [("provider-a", PROVIDER | LEDGER_REPLICATOR), ("provider-b", PROVIDER | LEDGER_REPLICATOR),
         ("auditor", AUDITOR), ("replicator", LEDGER_REPLICATOR), ("freeloader", AUDITOR)]


def prepare_lab(root, source, host="10.89.88", canary=True):
    if root.exists():
        raise ValueError("lab directory already exists; choose a new root")
    original, original_objects = open_node(source, read_only=True)
    original.verify_history()
    root.mkdir(parents=True)
    authority = keygen(root / "authority/key.private.pem", root / "authority/key.public")
    members = {}
    for index, (name, roles) in enumerate(NODES):
        directory = root / name
        public = keygen(directory / "key.private.pem", directory / "key.public")
        members[public] = {"name": name, "roles": roles, "endpoint": f"http://{host}.{10 + index}:8900"}
    policy = {"schema": "dawl.swarm-policy/1", "authority": authority, "lineage_policy": original.policy, "members": members}
    pin = validate_policy(policy)
    write_json(root / "policy.json", policy)
    authority_key = load_key(root / "authority/key.private.pem")
    provider_keys = [p for p, m in members.items() if m["roles"] & PROVIDER]
    canary_entry = None
    for index, (public, member) in enumerate(members.items()):
        directory = root / member["name"]
        write_json(directory / "trust.json", original.trust)
        write_json(directory / "swarm-policy.json", policy)
        write_json(directory / "admission.json", admit(policy, public, authority_key))
        write_json(directory / "swarm.json", {"policy_sha256": pin, "bootnodes": provider_keys, "samples": 16, "lab_faults": True})
        write_json(directory / "fault.json", {"delay_ms": 0, "corrupt": False, "withhold": bool(member["roles"] & PROVIDER),
                   "drop_every": 0, "partition": False, "freeloader": member["name"] == "freeloader"})
        ledger, objects = open_node(directory)
        if member["roles"] & PROVIDER:
            for entry in original.entries():
                ledger.add(entry)
            # Actual independent copies, no shared writeable provider store or private contributor keys.
            for digest in lineage_files(ledger):
                shutil.copyfile(original_objects.root / digest, objects.root / digest)
            if canary:
                if canary_entry is None:
                    target = next(e for e in ledger.entries() if e["body"]["kind"] == "vectors")
                    payload = copy.deepcopy(target["body"]["payload"])
                    values = objects.read_json(payload["files"]["index"])
                    for value in values:
                        value["vectors_sha256"] = "0" * 64
                    payload["files"]["index"] = objects.json(values)
                    payload["lab_canary"] = "deliberately false vector hashes; isolated swarm fixture only"
                    canary_entry = ledger.create("vectors", payload, target["body"]["parents"],
                                                  load_key(source / "identities/vector/key.private.pem"))
                    canary_bytes = canonical_json(values)
                else:
                    objects.put(canary_bytes)
                    ledger.add(canary_entry)
    report = {"schema": "dawl.swarm-lab/1", "policy_sha256": pin, "lineage_policy": original.policy,
              "source_checkpoint": original.checkpoint(), "canary_entry": canary_entry["id"] if canary_entry else None,
              "nodes": [{"name": n, "url": f"http://127.0.0.1:{8900+i}", "public": p}
                        for i, ((n, _), p) in enumerate(zip(NODES, members))]}
    write_json(root / "lab.json", report)
    return report
