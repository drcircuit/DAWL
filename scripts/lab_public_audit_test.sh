#!/bin/sh
set -eu
kit=/var/lib/dawl/releases/hypertrix-home-lab-preview-003
root=/var/lib/dawl/seed-lab-001
podman run --name hypertrix-lab-integration-004 --network host --cpus 4 --memory 4g \
  -v "$kit:/work:ro" -v "$root:/lab" localhost/hypertrix-lab:004 \
  /work/scripts/lab_integration.py --root /lab/integration-004 > "$root/integration-004.log" 2>&1
public=$(cat "$root/integration-004/run/operator.public.key")
mkdir -p "$root/independent-audit-space"
podman run --name hypertrix-lab-public-auditor-002 --network none --cpus 4 --memory 4g \
  -v "$kit:/work:ro" -v "$root/integration-004/consumer:/public:ro" -v "$root/independent-audit-space:/audit" localhost/hypertrix-lab:004 \
  /work/lab/replay.py --run /public/run --dataset /public/dataset --root /audit/result \
  --start 1 --end 3 --device cpu --expected-operator "$public" > "$root/independent-audit-001.log" 2>&1
