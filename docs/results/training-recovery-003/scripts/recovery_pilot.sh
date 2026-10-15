#!/bin/sh
# Isolated recovery trial. Never resets existing containers, identities or state.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
run="${HYPERTRIX_RECOVERY_RUN:-003}"
case "$run" in [0-9][0-9][0-9]) ;; *) echo 'Three-digit run required' >&2; exit 2;; esac
native="/var/lib/dawl/training-recovery-$run"
state="$native/state"
image="localhost/hypertrix-training-recovery:$run"
prefix="hypertrix-r6-$run"
helper=/app/scripts/recovery_pilot.py

start() {
  name="$1"; mount="$2"; gpu="$3"; shift 3
  if podman container exists "$prefix-$name"; then echo 'Fresh container name required' >&2; exit 1; fi
  if [ "$gpu" = yes ]; then
    set -- --device /dev/dxg -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro "$image" -P "$@"
  else set -- "$image" -P "$@"; fi
  podman run -d --init --name "$prefix-$name" --label "org.hypertrix.recovery=$run" --network host \
    --read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges \
    --cpus 2 --memory 5g --pids-limit 256 -v "$mount:/node" --entrypoint python "$@"
}
helper() {
  podman run --rm --init --name "$prefix-helper" --label "org.hypertrix.recovery=$run" --network host \
    --cap-drop ALL --security-opt no-new-privileges --device /dev/dxg \
    -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro \
    -v "$native:/trial" --entrypoint python "$image" -P "$helper" "$@" --root /trial/state
}
worker() {
  # The signing identity persists across stage-specific stores. Preserve its
  # discovery counter so restarting a member cannot roll back signed adverts.
  for previous in a b; do
    [ "$previous" = "$1" ] && break
    if [ -f "$state/runs/$previous/$2/dht-sequence.json" ]; then
      cp "$state/runs/$previous/$2/dht-sequence.json" "$state/runs/$1/$2/dht-sequence.json"
      chown 1000:1000 "$state/runs/$1/$2/dht-sequence.json"
    fi
  done
  start "$1-$2" "$state/runs/$1/$2" yes -m dawl_training.oracle "${2%-*}"
}
stop_stage() {
  for node in trainer-0 trainer-1 trainer-2 verifier-0 verifier-1; do
    if podman container exists "$prefix-$1-$node"; then podman stop --time 15 "$prefix-$1-$node"; fi
  done
}
case "${1:-status}" in
 build)
  if podman image exists "$image"; then echo 'Pinned image already exists; choose a fresh run ID.' >&2; exit 1; fi
  podman build -f "$repo/Dockerfile.oracle-training" -t "$image" "$repo" ;;
 prepare)
  [ ! -e "$native" ] || { echo 'Fresh state root required' >&2; exit 1; }
  mkdir "$native"; chown 1000:1000 "$native"
  podman run --rm --network none --cap-drop ALL -v "$native:/pilot" -v "$repo:/repo:ro" \
    -v /var/lib/dawl/training-001/state/provider:/source:ro \
    -v /var/lib/dawl/profile-v3-002/state/proposals.json:/profile-source/proposals.json:ro \
    --entrypoint python "$image" -P "$helper" prepare --root /pilot/state ;;
 trial)
  # External timeout may terminate this shell; stop only this trial's GPU workers.
  trap 'stop_stage a; stop_stage b; stop_stage c' EXIT HUP INT TERM
  i=0; while [ "$i" -lt 4 ]; do start "validator-$i" "$state/validator-$i" no /app/scripts/cluster_entrypoint.py validator /node; i=$((i+1)); done
  start provider "$state/provider" no /app/dawl_profiles.py provider --root /node --host 127.70.0.28 --port 8900
  start discovery "$state/provider" no "$helper" dht --root /node
  helper bootstrap
  start worker-0 "$state/worker-0" no /app/dawl_profiles.py worker --root /node --once
  start worker-1 "$state/worker-1" no /app/dawl_profiles.py worker --root /node --once
  helper source-finish
  helper source-evidence
  helper activate
  helper stage --stage a
  podman restart "$prefix-provider"
  worker a trainer-0
  helper submitted --stage a
  podman stop --time 15 "$prefix-a-trainer-0"
  worker a trainer-1
  worker a verifier-0
  worker a verifier-1
  helper reject
  worker a trainer-2
  helper complete --stage a
  stop_stage a
  helper stage --stage b
  podman restart "$prefix-provider"
  worker b trainer-1
  worker b trainer-2
  worker b verifier-0
  helper cancel
  stop_stage b
  helper stage --stage c
  podman restart "$prefix-provider"
  worker c trainer-1
  worker c trainer-2
  worker c verifier-0
  worker c verifier-1
  helper complete --stage c
  helper export
  # Completed GPU workers release VRAM at exit; validators/provider retain evidence.
  ;;
 helper) shift; helper "$@" ;;
 stop)
  for container in $(podman ps --filter "label=org.hypertrix.recovery=$run" --format '{{.Names}}'); do
    podman stop --time 15 "$container"
  done ;;
 status) podman ps -a --filter "label=org.hypertrix.recovery=$run" --format 'table {{.Names}}\t{{.Status}}' ;;
 *) echo 'Usage: build|prepare|trial|helper COMMAND|status' >&2; exit 2 ;;
esac
