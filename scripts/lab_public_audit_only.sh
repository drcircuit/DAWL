#!/bin/sh
set -eu
root=/var/lib/dawl/seed-lab-001
kit=/var/lib/dawl/releases/hypertrix-home-lab-preview-003
public=$(cat "$root/integration-004/run/operator.public.key")
mkdir -p "$root/isolated-auditor"
podman run --name hypertrix-lab-isolated-auditor --network none --cpus 4 --memory 4g \
  --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=128m \
  -v "$kit:/work:ro" -v "$root/integration-004/consumer:/public:ro" -v "$root/isolated-auditor:/audit" localhost/hypertrix-lab:004 \
  /work/lab/replay.py --run /public/run --dataset /public/dataset --root /audit/result \
  --start 1 --end 3 --device cpu --expected-operator "$public" > "$root/isolated-auditor.log" 2>&1
