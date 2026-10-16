#!/bin/sh
# Add a separate CPU evidence path without modifying the frozen trainer/runtime.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/evidence-001
lab=/var/lib/dawl/seed-lab-001
image=localhost/hypertrix-lab:004
blueprint=4b7331add9246e4c966cbb67478687755f9daf5bfe5c19124effcb41affb7c63
mkdir -p "$root"
if [ ! -d "$root/runtime-v2" ]; then
  mkdir "$root/runtime-v2"
  cp -a "$repo/src" "$root/runtime-v2/src"
fi
if [ ! -f "$root/index/index.json" ]; then
  podman run --rm --init --name hypertrix-evidence-indexer --network none --cpus 2 --memory 5g \
    --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=256m \
    --label org.hypertrix.lab-role=evidence-indexer --label "org.hypertrix.work-root=$root/index" \
    -v "$root/runtime-v2:/work:ro" -v "$lab/dataset:/dataset:ro" -v "$lab/curated-v2:/corpus:ro" -v "$root:/evidence" \
    "$image" -m dawl_evidence index --dataset /dataset --corpus /corpus --index /evidence/index --blueprint "$blueprint"
fi
python3 - "$root" "$blueprint" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);blueprint=sys.argv[2]
policy={'schema':'hypertrix.lab-network/1','allowed_cidrs':['127.80.0.0/24']}
(root/'network.json').write_text(json.dumps(policy))
for name in ('provider-peer','consumer-peer'):
    (root/name).mkdir(exist_ok=True)
    (root/name/'profile-capabilities.json').write_text(json.dumps([blueprint]))
PY
network=$(python3 -c 'import hashlib,json,sys; p=json.load(open(sys.argv[1])); print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest())' "$root/network.json")
for name in provider consumer; do
  if [ "$name" = provider ]; then host=127.80.0.13; role=EVIDENCE_PROVIDER; seed=http://127.80.0.14:8913
  else host=127.80.0.14; role=CONSUMER; seed=http://127.80.0.13:8913; fi
  if ! podman container exists "hypertrix-evidence-$name-mesh"; then
    podman run -d --init --name "hypertrix-evidence-$name-mesh" --network host --cpus .5 --memory 512m \
      --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=64m \
      --label org.hypertrix.lab-role=discovery --label "org.hypertrix.work-root=$root/$name-peer" \
      -v "$root/runtime-v2:/work:ro" -v "$root/$name-peer:/peer" -v "$root/network.json:/network.json:ro" "$image" -m dawl_lab.mesh \
      --root /peer --network /network.json --network-id "$network" \
      --name "evidence-$name" --bind "$host" --advertise "$host" --roles "$role" --seeds "$seed"
  fi
done
# The provider shares only its own discovery identity. Wait for its key creation.
attempt=0
while [ ! -s "$root/provider-peer/key.public.hex" ]; do
  attempt=$((attempt+1)); [ "$attempt" -le 20 ] || exit 1
  sleep 1
done
if ! podman container exists hypertrix-evidence-provider; then
  podman run -d --init --name hypertrix-evidence-provider --network host --cpus 2 --memory 4g \
    --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=128m \
    --label org.hypertrix.lab-role=evidence-provider --label "org.hypertrix.work-root=$root/index" \
    -v "$root/runtime-v2:/work:ro" -v "$lab/dataset:/dataset:ro" -v "$lab/curated-v2:/corpus:ro" \
    -v "$root/index:/index:ro" -v "$root/provider-peer:/identity:ro" \
    "$image" -m dawl_evidence serve --dataset /dataset --corpus /corpus --index /index \
    --blueprint "$blueprint" --identity /identity --bind 127.80.0.13
fi
printf 'Evidence provider: http://127.80.0.13:8914/health\nConsumer discovery: http://127.80.0.14:8913/dht/roles/EVIDENCE_PROVIDER\n'
