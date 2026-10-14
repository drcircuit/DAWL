#!/bin/sh
# New identities and state; the five-profile historical run remains frozen.
set -eu
root=/var/lib/dawl/o200k-gpu-001
image=localhost/hypertrix-o200k-gpu:v1
case "${1:-}" in
  init)
    test ! -e "$root"
    mkdir -p "$root"
    podman run --rm --network none --user 0 -v "$root:/experiment" -v "$PWD:/repo:ro" \
      -v /var/lib/dawl/profile-v3-002/state/provider:/source:ro "$image" init --root /experiment/state
    chown -R 1000:1000 "$root"
    ;;
  prepare)
    podman run --rm --network none --cpus 3 --memory 10g -v "$root/state:/run" -v "$PWD:/repo:ro" \
      "$image" prepare --name o200k_base
    ;;
  provider)
    podman run --rm --network none -v "$root/state:/run" "$image" index
    podman run -d --name hypertrix-o200k-gpu-cortex --network host --cpus 2 --memory 1g \
      --cap-drop ALL --security-opt no-new-privileges -v "$root/state/public:/node:ro" \
      --entrypoint python "$image" /app/dawl_profiles.py provider --root /node --host 127.70.0.29 --port 8900
    ;;
  replay)
    podman run --rm --name hypertrix-o200k-gpu --network host --cpus 3 --memory 10g \
      --cap-drop ALL --security-opt no-new-privileges --device /dev/dxg -v /usr/lib/wsl:/usr/lib/wsl:ro \
      -v "$root/state/gpu:/run" -v "$root/state/public/evidence:/public/evidence:ro" \
      -v "$root/state/public/jobs:/public/jobs:ro" -v "$root/state/public/identity.json:/public/identity.json:ro" \
      "$image" replay --name o200k_base --provider http://127.70.0.29:8900
    ;;
  finish)
    podman run --rm --network none -v "$root/state:/run" "$image" summarize --names o200k_base
    mkdir -p docs/results/o200k-gpu-run-001
    cp "$root"/state/results/*.json docs/results/o200k-gpu-run-001/
    podman stop hypertrix-o200k-gpu-cortex
    ;;
  *) echo 'Usage: o200k_gpu_pilot.sh init|prepare|provider|replay|finish' >&2; exit 2 ;;
esac
