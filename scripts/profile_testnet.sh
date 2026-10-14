#!/bin/sh
# Isolated local pilot. No Azure commands, inherited validator keys or live mounts.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
run="${DAWL_PROFILE_RUN:-002}"
case "$run" in [0-9][0-9][0-9]) ;; *) echo 'Run must have three decimal digits' >&2; exit 2;; esac
native="/var/lib/dawl/profile-v3-$run"
image=localhost/hypertrix-profiles:v3
label=org.hypertrix.profile-pilot
helper="$repo/scripts/profile_testnet.py"
state="$native/state"
start() {
  name="$1"; role="$2"; shift 2
  memory=1g
  case "$role" in worker-*) memory=3g ;; esac
  if podman container exists "hypertrix-p3-$run-$name"; then echo "Container already exists: $name" >&2; exit 1; fi
  podman run -d --name "hypertrix-p3-$run-$name" --label "$label=$run" --network host \
    --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges \
    --cpus 1 --memory "$memory" --pids-limit 128 -v "$state/$role:/node" \
    -v "$helper:/runner.py:ro" --entrypoint python "$image" "$@"
}
case "${1:-status}" in
  prepare)
    [ ! -e "$native" ] || { echo 'Fresh pilot root required; identities will not be overwritten.' >&2; exit 1; }
    mkdir -p "$native"
    chown 1000:1000 "$native"
    if [ -n "${DAWL_PROFILE_PUBLIC_CACHE:-}" ]; then
      podman run --rm --network none --label "$label=$run" --cap-drop ALL \
        -v "$native:/pilot" -v "$repo:/repo:ro" \
        -v "$DAWL_PROFILE_PUBLIC_CACHE/provider/objects:/cache/objects:ro" \
        -v "$DAWL_PROFILE_PUBLIC_CACHE/proposals.json:/cache/proposals.json:ro" --entrypoint python "$image" \
        /repo/scripts/profile_testnet.py prepare --root /pilot/state --repository /repo --public-cache /cache
    else
      podman run --rm --network none --label "$label=$run" --cap-drop ALL \
        -v "$native:/pilot" -v "$repo:/repo:ro" --entrypoint python "$image" \
        /repo/scripts/profile_testnet.py prepare --root /pilot/state --repository /repo
    fi
    ;;
  up)
    i=0
    while [ "$i" -lt 4 ]; do
      start "validator-$i" "validator-$i" /app/scripts/cluster_entrypoint.py validator /node
      i=$((i+1))
    done
    start provider provider /app/dawl_profiles.py provider --root /node --host 127.70.0.27 --port 8900
    start discovery provider /runner.py dht --root /node
    ;;
  bootstrap|finish|evidence)
    podman run --rm --name "hypertrix-p3-$run-$1" --label "$label=$run" --network host \
      --cap-drop ALL --security-opt no-new-privileges -v "$state:/pilot" -v "$helper:/runner.py:ro" \
      --entrypoint python "$image" /runner.py "$1"
    ;;
  workers)
    start worker-0 worker-0 /app/dawl_profiles.py worker --root /node --once
    start worker-1 worker-1 /app/dawl_profiles.py worker --root /node --once
    ;;
  status) podman ps -a --filter "label=$label=$run" --format 'table {{.Names}}\t{{.Status}}' ;;
  stop)
    for name in $(podman ps --filter "label=$label=$run" --format '{{.Names}}'); do
      podman container exists "$name" || continue
      [ "$(podman inspect --format '{{ index .Config.Labels "org.hypertrix.profile-pilot" }}' "$name")" = "$run" ]
      podman stop --time 20 "$name"
    done
    ;;
  *) echo 'Usage: profile_testnet.sh prepare|up|bootstrap|workers|finish|evidence|status|stop' >&2; exit 2 ;;
esac
