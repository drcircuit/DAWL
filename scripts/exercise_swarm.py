"""Exercise the already-running isolated lab; retain the measured phases and signed evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dawl_node.common import canonical_json, read_json, sha256, write_json
from dawl_swarm.transfer import catalog


def get(port, route="/swarm/status"):
    for attempt in range(3):
        try:
            with urlopen(f"http://127.0.0.1:{port}{route}", timeout=8) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("artifacts/swarm-lab-001"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/swarm-run-001"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    lab = read_json(args.root / "lab.json")
    phases = []
    start = time.perf_counter()

    def record(name, detail):
        item = {"name": name, "elapsed_seconds": round(time.perf_counter() - start, 3), "detail": detail}
        phases.append(item)
        write_json(args.output / f"{len(phases):02}-{name}.json", item)
        print(f"{name}: {item['elapsed_seconds']} s", flush=True)

    def wait(test, seconds=240):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                result = test()
                if result:
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise TimeoutError("lab phase did not reach its acceptance condition")

    def fault(name, **changes):
        path = args.root / name / "fault.json"
        versions = sorted((path.parent / "faults").glob("*.json"))
        value = {**read_json(versions[-1] if versions else path), **changes}
        write_json(path.parent / "faults" / f"{time.time_ns():020d}.json", value)

    def podman(action, name):
        container = "dawl-swarm-" + name
        prefix = ["wsl", "-d", "kali-linux", "-u", "root", "--exec", "podman"]
        label = subprocess.check_output(prefix + ["inspect", "--format", '{{ index .Config.Labels "org.dawl.swarm" }}', container], text=True).strip()
        if label != "v1":
            raise ValueError("refusing to operate on an unrelated container")
        subprocess.run(prefix + [action, container], check=True, capture_output=True, timeout=40)

    def own_receipts(state):
        return [r["envelope"] for r in state["receipts"] if r["envelope"]["body"]["signer"] == state["public"]]

    def withheld():
        state = get(8902)
        failures = [r for r in own_receipts(state) if r["body"]["outcome"] == "unavailable"]
        return state if len({r["body"]["vectors_entry"] for r in failures}) == 9 else None

    try:
        wait(lambda: all(get(8900+i)["checkpoint"]["entries"] == 37 for i in range(5)))
        initial = wait(withheld)
        record("all-providers-withhold", initial)
        before_failures = len([r for r in own_receipts(initial) if r["body"]["outcome"] == "unavailable"])
        fault("replicator", partition=True)
        record("partition-replicator", get(8903))
        # A sends corrupt data; B adds delay and deterministic request loss.
        fault("provider-a", withhold=False, corrupt=True)
        fault("provider-b", withhold=False, delay_ms=40, drop_every=11)
        policy = read_json(args.root / "provider-a/trust.json")
        table_hash = policy["anchors"]["embedding_sha256"]
        table_path = args.root / "provider-a/objects" / table_hash
        listing = catalog(table_path, {"sha256": table_hash, "bytes": table_path.stat().st_size})

        def partial_table():
            present = sum((args.root / "auditor/pieces" / h).exists() for h in listing["pieces"])
            return {"table_pieces": present, "total_pieces": len(listing["pieces"]), "state": get(8902)} if (
                present >= 8 and not (args.root / "auditor/objects" / table_hash).exists()) else None

        record("partial-table-transfer", wait(partial_table))
        podman("stop", "provider-b")
        record("honest-provider-stopped", get(8902))

        def outage_observed():
            state = get(8902)
            failures = [r for r in own_receipts(state) if r["body"]["outcome"] == "unavailable"]
            return state if len(failures) > before_failures else None

        record("interrupted-transfer-unavailable", wait(outage_observed))
        # Leave A corrupt, restore B without loss so every artifact has one available honest source.
        fault("provider-b", drop_every=0, delay_ms=15)
        podman("start", "provider-b")

        def completed():
            state = get(8902)
            receipts = own_receipts(state)
            passes = {r["body"]["vectors_entry"] for r in receipts if r["body"]["outcome"] == "pass" and r["body"]["rechecks"] is None}
            rechecked = {r["body"]["rechecks"] for r in receipts if r["body"]["rechecks"] is not None and r["body"]["outcome"] in ("pass", "invalid")}
            return state if len(passes) == 8 and len(rechecked) == 9 and state["disputes"] else None

        final_audit = wait(completed, seconds=600)
        assert final_audit["counters"]["corrupt_pieces_rejected"] > 0
        assert final_audit["counters"]["piece_cache_hits"] > 0
        record("recovered-and-false-approval-detected", final_audit)
        fault("provider-a", corrupt=False)
        fault("provider-b", delay_ms=0)
        fault("replicator", partition=False)

        def converged():
            states = [get(8900 + i) for i in range(5)]
            checkpoints = {s["checkpoint"]["set_sha256"] for s in states}
            receipt_sets = {s["receipt_set_sha256"] for s in states}
            return states if len(checkpoints) == 1 and len(receipt_sets) == 1 else None

        final = wait(converged)
        record("partition-healed-convergence", final)
        from dawl_platform.workflow import open_node, implementation_digest
        baseline = open_node(Path("artifacts/platform-v1"), read_only=True)[0].checkpoint()
        assert baseline == lab["source_checkpoint"]
        report = {"schema": "dawl.swarm-run/1", "created_at": datetime.now(timezone.utc).isoformat(),
                  "accepted": True, "implementation_sha256": implementation_digest(),
                  "phases": [{"name": p["name"], "elapsed_seconds": p["elapsed_seconds"]} for p in phases],
                  "baseline_unchanged": baseline, "canary_entry": lab["canary_entry"],
                  "final_states": final, "scope": "Five CPU containers, one physical host; application-level delay/loss/partition surrogates. No internet-scale claim."}
        write_json(args.output / "report.json", report)
        print(canonical_json({"accepted": True, "elapsed_seconds": time.perf_counter() - start,
                              "receipts": final[2]["receipt_count"], "disputes": len(final[2]["disputes"])}).decode())
    finally:
        # A failed experiment must not leave providers deliberately withholding indefinitely.
        for name in ("provider-a", "provider-b", "replicator"):
            fault(name, withhold=False, corrupt=False, partition=False, drop_every=0, delay_ms=0)
        podman("start", "provider-b")


if __name__ == "__main__":
    main()
