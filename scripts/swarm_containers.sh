#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root="${DAWL_SWARM_ROOT:-$(pwd)/artifacts/swarm-lab-001}"
image=localhost/dawl-swarm:0.1
network=dawl-swarm-lab
nodes="provider-a provider-b auditor replicator freeloader"
case "${1:-status}" in
  build) podman build -f Dockerfile.swarm -t "$image" . ;;
  up)
    for node in $nodes; do
      [ -f "$root/$node/swarm.json" ]
      if podman container exists "dawl-swarm-$node"; then echo "Container already exists: $node" >&2; exit 1; fi
    done
    if podman network exists "$network"; then
      [ "$(podman network inspect --format '{{ index .Labels "org.dawl.swarm" }}' "$network")" = v1 ]
    else
      podman network create --disable-dns --subnet 10.89.88.0/24 --label org.dawl.swarm=v1 "$network"
    fi
    address=10
    port=8900
    for node in $nodes; do
      podman run -d --name "dawl-swarm-$node" --label org.dawl.swarm=v1 --network "$network" --ip "10.89.88.$address" \
        -p "127.0.0.1:$port:8900" --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges \
        --cpus 2 --memory 2g --pids-limit 128 -v "$root/$node:/node" \
        "$image" serve --root /node --host 0.0.0.0 --port 8900 --allow-insecure-lab
      address=$((address + 1))
      port=$((port + 1))
    done
    ;;
  down)
    for node in $nodes; do
      name="dawl-swarm-$node"
      if podman container exists "$name"; then
        [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.swarm" }}' "$name")" = v1 ]
        podman stop --time 15 "$name"
        podman rm "$name"
      fi
    done
    if podman network exists "$network"; then
      [ "$(podman network inspect --format '{{ index .Labels "org.dawl.swarm" }}' "$network")" = v1 ]
      podman network rm "$network"
    fi
    ;;
  status) podman ps -a --filter label=org.dawl.swarm=v1 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  *) echo "Usage: sh scripts/swarm_containers.sh [build|up|down|status]" >&2; exit 2 ;;
esac
