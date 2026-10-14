#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
run="${DAWL_TRAINING_RUN:-003}"
case "$run" in [0-9][0-9][0-9]) ;; *) exit 2;; esac
native="/var/lib/dawl/oracle-training-$run"
state="$native/state"
observer="$native/observer"
exchange="$native/console-telemetry"
name="hypertrix-t4-$run-console"
serve() {
    [ -f "$repo/output/testnet-presentation/index.json" ] || { echo 'Build the public experiment presentation first.' >&2; exit 1; }
    podman run -d --init --name "$name" --label "org.hypertrix.oracle-training=$run" --network host \
      --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 1g \
      -v "$observer:/observer" -v "$exchange:/telemetry:ro" \
      -v "$repo/scripts/local_testnet_console.py:/console.py:ro" \
      -v "$repo/scripts/local_testnet_console.html:/console.html:ro" \
      -v "$repo/scripts/local_testnet_experiments.html:/experiments.html:ro" \
      -v "$repo/scripts/local_testnet_branches.html:/branches.html:ro" \
      -v "$repo/docs/results/collaboration-rehearsal-001:/collaboration-rehearsal:ro" \
      -v "$repo/output/testnet-presentation:/presentation:ro" \
      --entrypoint python "localhost/hypertrix-oracle-training:$run" -P /console.py
}
case "${1:-status}" in
  start)
    [ ! -e "$observer" ] || { echo 'Observer state exists; use the existing service.' >&2; exit 1; }
    mkdir -p "$observer" "$exchange"
    cp "$state/publisher/plan.json" "$state/publisher/genesis.json" "$state/publisher/cluster.json" "$observer/"
    chown -R 1000:1000 "$observer" "$exchange"
    nohup python3 "$repo/scripts/local_testnet_telemetry.py" --root "$state" --exchange "$exchange" --run "$run" > "$native/console-collector.log" 2>&1 < /dev/null &
    echo "$!" > "$native/console-collector.pid"
    serve
    ;;
  refresh)
    [ -f "$repo/output/testnet-presentation/index.json" ] && [ -f "$observer/cluster.json" ]
    [ "$(podman inspect --format '{{ index .Config.Labels "org.hypertrix.oracle-training" }}' "$name")" = "$run" ]
    podman stop --time 20 "$name"
    # Only the observer container is replaced. Its state and the host collector remain.
    podman rm "$name"
    serve
    ;;
  status) podman ps -a --filter "name=$name" --format '{{.Names}} {{.Status}}' ;;
  *) echo 'Usage: start|refresh|status' >&2; exit 2 ;;
esac
