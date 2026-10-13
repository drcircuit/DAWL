"""Validate live peers; optionally stop the labelled source peer and replay from its replica."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import read_json, write_json
from dawl_platform.artifacts import PublicPeer
from dawl_platform.auditor import verify_public
from dawl_platform.workflow import open_node, implementation_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/platform-v1"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/platform-deployment-check-001"))
    parser.add_argument("--exercise-peer-outage", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    ledger, _ = open_node(args.root, read_only=True)
    expected = ledger.verify_history()
    peers = {port: PublicPeer(f"http://127.0.0.1:{port}") for port in (8880, 8890, 8891)}
    checkpoints = {str(port): peer.json("/api/checkpoint") for port, peer in peers.items()}
    if any(checkpoint != expected for checkpoint in checkpoints.values()):
        raise ValueError("live peers have not converged")
    command = ["wsl", "-d", "kali-linux", "-u", "root", "--exec", "podman"]
    import json
    containers = {}
    for name in ("dawl-platform-artifacts", "dawl-platform-auditor"):
        info = json.loads(subprocess.check_output(command + ["inspect", name]))[0]
        if info["Config"]["Labels"].get("org.dawl.platform") != "v1":
            raise ValueError("container ownership label mismatch")
        mounts = [{"destination": m["Destination"], "writable": m["RW"]} for m in info["Mounts"]]
        containers[name] = {"image": info["Image"], "user": info["Config"]["User"], "mounts": mounts,
                            "read_only_root": info["HostConfig"]["ReadonlyRootfs"]}
    if {m["destination"] for m in containers["dawl-platform-auditor"]["mounts"]} != {"/node", "/identity/key.private.pem"}:
        raise ValueError("unexpected auditor mount")
    if any(m["writable"] for m in containers["dawl-platform-artifacts"]["mounts"]):
        raise ValueError("artifact peer received a writable host mount")
    replay = None
    if args.exercise_peer_outage:
        replica = args.output / "public-verifier"
        replica.mkdir()
        write_json(replica / "trust.json", ledger.trust)
        try:
            subprocess.run(command + ["stop", "--time", "10", "dawl-platform-artifacts"], check=True)
            stopped = json.loads(subprocess.check_output(command + ["inspect", "dawl-platform-artifacts"]))[0]
            if stopped["State"]["Running"]:
                raise ValueError("source peer did not stop")
            print("Source artifact peer offline; replaying from replica into a fresh cache", flush=True)
            replay = verify_public(replica, "http://127.0.0.1:8891")
            if not replay["accepted"] or replay["checkpoint"] != expected:
                raise ValueError("replica failed complete public replay")
        finally:
            subprocess.run(command + ["start", "dawl-platform-artifacts"], check=True)
        for _ in range(50):
            try:
                if peers[8890].json("/api/checkpoint") != expected:
                    raise ValueError("source peer changed after restart")
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise ValueError("source peer did not recover")
    report = {"schema": "dawl.platform-deployment/1", "accepted": True,
              "recorded_utc": datetime.now(timezone.utc).isoformat(), "implementation_sha256": implementation_digest(),
              "checkpoints": checkpoints, "containers": containers, "outage_exercised": args.exercise_peer_outage,
              "replica_replay": replay, "source_peer_recovered": peers[8890].json("/health")["status"] == "ok",
              "scope": "One physical host; manual alternate-peer selection. No automatic failover or internet-scale claim."}
    write_json(args.output / "report.json", report)
    print({"accepted": True, "outage_exercised": args.exercise_peer_outage, "ledger_entries": expected["entries"]})


if __name__ == "__main__":
    main()
