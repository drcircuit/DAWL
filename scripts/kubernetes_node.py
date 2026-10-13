"""One durable identity per pod; public bootstrap mounted separately from private state."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, "/app/src")
from dawl_cluster.external import identity, install, validate_registration, check_bundle
from dawl_platform.workflow import implementation_digest


def main():
    root = Path("/data/node")
    name, role, host = (os.environ[k] for k in ("DAWL_NAME", "DAWL_ROLE", "DAWL_HOST"))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not root.exists():
        identity(root, name, role, host)
    member = validate_registration(json.loads((root / "registration.json").read_text()))
    if (member["name"], member["role"], member["host"]) != (name, role, host):
        raise ValueError("PVC identity differs from assigned role; refusing to reset")
    print("Public registration ready; awaiting agreed network pin", flush=True)
    pin_file = Path("/bootstrap/network.sha256")
    while not pin_file.exists():
        if stopping:
            return
        time.sleep(1)
    pin = pin_file.read_text().strip()
    check_bundle("/bootstrap", pin)
    if not (root / "installed.json").exists():
        install(root, "/bootstrap", pin, bind_host="0.0.0.0")
    elif json.loads((root / "installed.json").read_text())["network_sha256"] != pin:
        raise ValueError("PVC belongs to a different network; refusing to reset")
    plan = json.loads((root / "plan.json").read_text())
    if plan['implementation_sha256'] != implementation_digest():
        raise ValueError('runtime source differs from the installed plan; refusing to start')
    # Providers receive allowlisted objects before they are released to serve.
    gate = "provider-ready" if role == "provider" else "start"
    while not (Path("/data") / gate).exists():
        if stopping:
            return
        time.sleep(1)
    if role == "publisher":
        while not stopping:
            time.sleep(1)
        return
    args = (["validator", str(root)] if role == "validator" else
            ["provider", "--root", str(root), "--host", "0.0.0.0", "--port", "8900", "--allow-insecure-lab"]
            if role == "provider" else ["worker", "--root", str(root)])
    child = subprocess.Popen([sys.executable, "/app/scripts/cluster_entrypoint.py", *args])
    try:
        while not stopping and child.poll() is None:
            time.sleep(.2)
    finally:
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=40)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    if not stopping and child.returncode:
        raise SystemExit(child.returncode)


if __name__ == "__main__":
    main()
