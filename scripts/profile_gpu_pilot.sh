#!/bin/sh
# Local isolated GPU diagnostic. Never mounts validator state or cloud secrets.
set -eu
root=/var/lib/dawl/profile-gpu-001
image=localhost/hypertrix-profile-gpu:v1
case "${1:-}" in
  init)
    test ! -e "$root"
    mkdir -p "$root"
    podman run --rm --network none --user 0 -v "$root:/experiment" -v "$PWD:/repo:ro" \
      -v /var/lib/dawl/profile-v3-002/state/provider:/source:ro "$image" init --root /experiment/state
    chown -R 1000:1000 "$root"
    ;;
  prepare)
    podman run --rm --network none --cpus 3 --memory 5g -v "$root/state:/run" -v "$PWD:/repo:ro" \
      "$image" prepare --name "$2"
    ;;
  provider)
    podman run --rm --network none -v "$root/state:/run" "$image" index
    podman run -d --name hypertrix-profile-gpu-cortex --network host --cpus 2 --memory 1g \
      --cap-drop ALL --security-opt no-new-privileges -v "$root/state/public:/node:ro" \
      --entrypoint python "$image" /app/dawl_profiles.py provider --root /node --host 127.70.0.28 --port 8900
    ;;
  replay)
    podman run --rm --name "hypertrix-profile-gpu-$2" --network host --cpus 3 --memory 5g \
      --cap-drop ALL --security-opt no-new-privileges --device /dev/dxg -v /usr/lib/wsl:/usr/lib/wsl:ro \
      -v "$root/state/gpu:/run" -v "$root/state/public/evidence:/public/evidence:ro" \
      -v "$root/state/public/jobs:/public/jobs:ro" -v "$root/state/public/identity.json:/public/identity.json:ro" \
      "$image" replay --name "$2"
    ;;
  refresh)
    podman run --rm --network none -v "$root/state:/run" "$image" index
    podman restart hypertrix-profile-gpu-cortex
    ;;
  finish)
    podman run --rm --network none -v "$root/state:/run" "$image" summarize
    mkdir -p docs/results/profile-gpu-run-001
    cp "$root"/state/results/*.json docs/results/profile-gpu-run-001/
    podman stop hypertrix-profile-gpu-cortex
    ;;
  *) echo 'Usage: profile_gpu_pilot.sh init|prepare NAME|provider|refresh|replay NAME|finish' >&2; exit 2 ;;
esac
