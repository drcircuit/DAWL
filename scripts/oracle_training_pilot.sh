#!/bin/sh
# Fresh local protocol-4 pilot; previous identities, images and evidence retained.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
run="${DAWL_TRAINING_RUN:-003}"
case "$run" in [0-9][0-9][0-9]) ;; *) echo 'Three digit run required' >&2; exit 2;; esac
native="/var/lib/dawl/oracle-training-$run"
state="$native/state"
image="localhost/hypertrix-oracle-training:$run"
label=org.hypertrix.oracle-training
helper=/app/scripts/oracle_training_pilot.py
start() {
  role="$1"; shift
  podman container exists "hypertrix-t4-$run-$role" && { echo 'Container exists; explicit recovery required' >&2; exit 1; }
  podman run -d --init --name "hypertrix-t4-$run-$role" --label "$label=$run" --network host \
    --read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges \
    --cpus 2 --memory 5g --pids-limit 256 --device /dev/dxg \
    -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$state/$role:/node" \
    --entrypoint python "$image" -P "$@"
}
case "${1:-status}" in
  build)
    if podman image exists "$image"; then echo 'Image tag already exists; choose a fresh run ID to preserve execution evidence.' >&2; exit 1; fi
    podman build -f "$repo/Dockerfile.oracle-training" -t "$image" "$repo"
    ;;
  prepare)
    [ ! -e "$native" ] || { echo 'Fresh root required; will not overwrite identities' >&2; exit 1; }
    mkdir -p "$native"; chown 1000:1000 "$native"
    podman run --rm --network none --cap-drop ALL -v "$native:/pilot" -v "$repo:/repo:ro" \
      -v /var/lib/dawl/training-001/state/provider:/source:ro \
      -v /var/lib/dawl/profile-v3-002/state/proposals.json:/profile-source/proposals.json:ro --entrypoint python "$image" "$helper" prepare --root /pilot/state
    ;;
  up)
    i=0; while [ "$i" -lt 4 ]; do start "validator-$i" /app/scripts/cluster_entrypoint.py validator /node; i=$((i+1)); done
    start provider /app/dawl_profiles.py provider --root /node --host 127.70.0.27 --port 8900
    podman run -d --init --name "hypertrix-t4-$run-discovery" --label "$label=$run" --network host \
      --cap-drop ALL --security-opt no-new-privileges -v "$state/provider:/node" --entrypoint python "$image" \
      /app/scripts/profile_testnet.py dht --root /node
    ;;
  bootstrap|source-finish|stage|replace|finish|evidence)
    podman run --rm --init --name "hypertrix-t4-$run-$1" --label "$label=$run" --network host \
      --cap-drop ALL --security-opt no-new-privileges -v "$state:/pilot" --entrypoint python "$image" "$helper" "$1"
    ;;
  source-workers)
    start worker-0 /app/dawl_profiles.py worker --root /node --once
    start worker-1 /app/dawl_profiles.py worker --root /node --once
    ;;
  abandon) start trainer-0 -m dawl_training.oracle trainer --abandon-after-claim ;;
  workers)
    start trainer-1 -m dawl_training.oracle trainer
    start verifier-0 -m dawl_training.oracle verifier
    start verifier-1 -m dawl_training.oracle verifier
    ;;
  replacement) start trainer-2 -m dawl_training.oracle trainer ;;
  status) podman ps -a --filter "label=$label=$run" --format 'table {{.Names}}\t{{.Status}}' ;;
  stop)
    for name in $(podman ps --filter "label=$label=$run" --format '{{.Names}}'); do
      [ "$(podman inspect --format '{{ index .Config.Labels "org.hypertrix.oracle-training" }}' "$name")" = "$run" ]
      podman stop --time 30 "$name"
    done
    ;;
  *) echo 'Usage: build|prepare|up|bootstrap|source-workers|source-finish|stage|abandon|workers|replace|replacement|finish|evidence|status|stop' >&2; exit 2 ;;
esac
