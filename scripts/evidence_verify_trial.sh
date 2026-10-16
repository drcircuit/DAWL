#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/evidence-001
lab=/var/lib/dawl/seed-lab-001
trial="$root/trial-v2"
consumer=$(cat "$root/consumer-peer/key.public.hex")
podman run --rm --init --name hypertrix-evidence-offline-verifier --network none --cpus 2 --memory 5g \
  --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=128m \
  -v "$root/runtime-v2:/work:ro" -v "$repo/scripts/evidence_verify_trial.py:/verify.py:ro" \
  -v "$trial:/trial:ro" -v "$lab/dataset:/dataset:ro" -v "$lab/curated-v2/texts:/corpus/texts:ro" \
  localhost/hypertrix-lab:004 /verify.py --trial /trial --dataset /dataset --corpus /corpus \
  --blueprint 4b7331add9246e4c966cbb67478687755f9daf5bfe5c19124effcb41affb7c63 \
  --consumer "$consumer" > "$trial/offline-verification.json"
cat "$trial/offline-verification.json"
