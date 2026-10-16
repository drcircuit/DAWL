#!/bin/sh
# Separate CPU Investigator; preserve every trainer and existing source service.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/investigator-001
evidence=/var/lib/dawl/evidence-001
lab=/var/lib/dawl/seed-lab-001
image=localhost/hypertrix-lab:004
blueprint=4b7331add9246e4c966cbb67478687755f9daf5bfe5c19124effcb41affb7c63
model_pin=811ca0d441e754022ded0e84f8952fa5d8e6379b2f1e354a8caeb0992feee8bb
network=e47803cfd40f0385c4434d8d27950d25937221547e61a5e815b642da3cc51b7a
if [ ! -d "$root/runtime-v1" ]; then
  mkdir "$root/runtime-v1"
  cp -a "$repo/src" "$root/runtime-v1/src"
fi
mkdir -p "$root/peer" "$root/consumer-bridge"
consumer=$(cat "$evidence/consumer-peer/key.public.hex")
PYTHONPATH="$root/runtime-v1/src" python3 -P - "$root" "$blueprint" <<'PY'
import json,sys
from pathlib import Path
from dawl_node.common import keygen,write_json
root=Path(sys.argv[1])
result=json.loads((root/'evaluation.json').read_bytes())
for name in ('peer','consumer-bridge'):
    path=root/name
    if not (path/'key.private.pem').exists():keygen(path/'key.private.pem',path/'key.public.hex')
    write_json(path/'profile-capabilities.json',[sys.argv[2]])
write_json(root/'peer/assessment-capabilities.json',{'evaluator_sha256':result['evaluator_sha256'],
                                                   'policy_sha256':result['policy_sha256']})
PY
for name in investigator bridge; do
  if [ "$name" = investigator ]; then
    host=127.80.0.15; role=EVIDENCE_ASSESSOR; identity=peer; seeds=http://127.80.0.16:8913
  else
    host=127.80.0.16; role=CONSUMER; identity=consumer-bridge; seeds=http://127.80.0.15:8913,http://127.80.0.13:8913
  fi
  if ! podman container exists "hypertrix-investigator-$name-mesh"; then
    podman run -d --init --name "hypertrix-investigator-$name-mesh" --network host --cpus .5 --memory 512m \
      --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=64m \
      --label org.hypertrix.lab-role=discovery --label "org.hypertrix.work-root=$root/$identity" \
      -v "$root/runtime-v1:/work:ro" -v "$root/$identity:/peer" -v "$evidence/network.json:/network.json:ro" \
      "$image" -m dawl_lab.mesh --root /peer --network /network.json --network-id "$network" \
      --name "investigator-$name" --bind "$host" --advertise "$host" --roles "$role" --seeds "$seeds"
  fi
done
if ! podman container exists hypertrix-investigator; then
  podman run -d --init --name hypertrix-investigator --network host --cpus 2 --memory 4g \
    --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:size=128m \
    -e HF_HUB_DISABLE_PROGRESS_BARS=1 -e HF_HUB_OFFLINE=1 \
    --label org.hypertrix.lab-role=evidence-assessor --label "org.hypertrix.work-root=$root" \
    -v "$root/runtime-v1:/work:ro" -v "$root/model:/model:ro" -v "$root/peer:/identity:ro" \
    -v "$evidence/network.json:/network.json:ro" -v "$lab/dataset:/dataset:ro" \
    "$image" -m dawl_evidence.assessment_service --dataset /dataset --blueprint "$blueprint" \
    --model /model --model-pin "$model_pin" --identity /identity --admit "$consumer" \
    --network /network.json --network-id "$network" --provider http://127.80.0.13:8914 --bind 127.80.0.15
fi
printf 'Investigator: http://127.80.0.15:8915/health\nDiscovery: http://127.80.0.16:8913/dht/roles/EVIDENCE_ASSESSOR\n'
