#!/bin/sh
# Fixed local training branch; all private state stays in separate native volumes.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/training-001/state
image=localhost/hypertrix-training:v1
label=org.hypertrix.training-pilot
peer() {
  number="$1"; name="$2"; shift 2
  podman run -d --name "$name" --label "$label=001" --network host --cpus 3 --memory 7g \
    --cap-drop ALL --security-opt no-new-privileges --device /dev/dxg \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$root/peer-$number:/node" \
    "$image" run --peer "$number" "$@"
}
case "${1:-status}" in
  build) podman build --network none -f Dockerfile.training -t "$image" . ;;
  prepare)
    test ! -e /var/lib/dawl/training-001
    mkdir -p /var/lib/dawl/training-001
    chown 1000:1000 /var/lib/dawl/training-001
    podman run --rm --network none --cpus 3 --memory 7g -v /var/lib/dawl/training-001:/pilot \
      -v "$repo:/repo:ro" -v /var/lib/dawl/profile-v3-002/state/provider:/source:ro \
      --entrypoint python "$image" /app/scripts/training_pilot.py prepare
    ;;
  start)
    podman run -d --name hypertrix-training-001-cortex --label "$label=001" --network host \
      --cpus 1 --memory 1g --cap-drop ALL --security-opt no-new-privileges \
      -v "$root/provider:/node:ro" --entrypoint python "$image" \
      /app/dawl_profiles.py provider --root /node --host 127.70.0.30 --port 8900
    peer 0 hypertrix-training-001-interrupted --stop-after-update 1
    peer 1 hypertrix-training-001-peer-1
    ;;
  resume) peer 0 hypertrix-training-001-peer-0 ;;
  audit)
    test -f "$root/peer-0/public/final.json"
    test -f "$root/peer-1/public/final.json"
    # Cold auditor must now obtain inputs and outputs from the training replicas.
    podman stop hypertrix-training-001-cortex
    podman run --rm --name hypertrix-training-001-auditor --label "$label=001" --network host \
      --cpus 3 --memory 7g --cap-drop ALL --security-opt no-new-privileges --device /dev/dxg \
      -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$root/auditor:/node" "$image" audit
    ;;
  export)
    destination="$repo/docs/results/training-run-001"
    mkdir -p "$destination"
    for name in stage.json dataset.json; do cp "$root/provider/$name" "$destination/$name"; done
    cp "$root/prepared.json" "$destination/prepared.json"
    for name in audit.json chain.json updates.json completions.json release.json challenge.json; do
      cp "$root/auditor/$name" "$destination/$name"
    done
    for i in 0 1; do
      cp "$root/peer-$i/public/transfers.jsonl" "$destination/peer-$i-transfers.jsonl"
    done
    cp "$root/auditor/public/transfers.jsonl" "$destination/auditor-transfers.jsonl"
    for name in hypertrix-training-001-interrupted hypertrix-training-001-peer-0 hypertrix-training-001-peer-1; do
      podman logs "$name" > "$destination/$name.log" 2>&1
    done
    ;;
  stop)
    for name in $(podman ps --filter "label=$label=001" --format '{{.Names}}'); do podman stop "$name"; done
    ;;
  status) podman ps -a --filter "label=$label=001" --format '{{.Names}} {{.Status}}' ;;
  *) echo 'Usage: training_pilot.sh build|prepare|start|resume|audit|export|stop|status' >&2; exit 2 ;;
esac
