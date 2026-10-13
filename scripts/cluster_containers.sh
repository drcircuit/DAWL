#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root="${DAWL_CLUSTER_ROOT:-$(pwd)/artifacts/cluster-v2}"
native="${DAWL_CLUSTER_NATIVE:-/var/lib/dawl/cluster-v2}"
native="$(realpath -m "$native")"
image=localhost/dawl-cluster:0.1
network=dawl-consensus-cluster
case "$native" in /var/lib/dawl/cluster-*) ;; *) echo 'Native state must be under /var/lib/dawl/cluster-*' >&2; exit 2;; esac
run_node() {
  name="$1"; address="$2"; port="$3"; shift 3
  if podman container exists "dawl-cluster-$name"; then echo "Already exists: $name" >&2; exit 1; fi
  # One native Linux state directory per container; validator private state is never shared.
  if [ "$port" = none ]; then
    podman run -d --name "dawl-cluster-$name" --label org.dawl.cluster=v1 --network "$network" --ip "$address" \
      --read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges \
      --cpus 2 --memory 2g --pids-limit 256 -v "$native/$name:/node" "$image" "$@"
  else
    podman run -d --name "dawl-cluster-$name" --label org.dawl.cluster=v1 --network "$network" --ip "$address" \
      -p "127.0.0.1:$port" --read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges \
      --cpus 2 --memory 2g --pids-limit 256 -v "$native/$name:/node" "$image" "$@"
  fi
}
case "${1:-status}" in
  build) podman build -f Dockerfile.cluster -t "$image" . ;;
  seed)
    [ -f "$root/deployment.json" ]
    if [ -e "$native" ]; then echo 'Native state exists; refusing to overwrite keys, WAL or application state' >&2; exit 1; fi
    mkdir -p "$native"
    cp -a "$root/." "$native/"
    chown -R 1000:1000 "$native"
    # Consensus keys and anti-double-signing state must be retained across restarts.
    find "$native" -name '*private.pem' -exec chmod 600 {} \;
    find "$native" -name 'priv_validator_key.json' -exec chmod 600 {} \;
    find "$native" -name 'node_key.json' -exec chmod 600 {} \;
    ;;
  up)
    [ -f "$native/deployment.json" ]
    if podman network exists "$network"; then
      [ "$(podman network inspect --format '{{ index .Labels "org.dawl.cluster" }}' "$network")" = v1 ]
    else
      podman network create --disable-dns --subnet 10.89.90.0/24 --label org.dawl.cluster=v1 "$network"
    fi
    i=0
    while [ "$i" -lt 4 ]; do
      run_node "validator-$i" "10.89.90.$((10+i))" "$((8920+i)):26657" validator /node
      i=$((i+1))
    done
    i=0
    while [ "$i" -lt 2 ]; do
      run_node "provider-$i" "10.89.90.$((20+i))" "$((8930+i)):8900" provider --root /node --host 0.0.0.0 --port 8900 --allow-insecure-lab
      i=$((i+1))
    done
    ;;
  workers)
    count="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["workers"]-1)' "$native/deployment.json")"
    i=0
    while [ "$i" -lt "$count" ]; do
      run_node "worker-$i" "10.89.90.$((30+i))" none worker --root /node
      i=$((i+1))
    done
    ;;
  worker)
    index="${2:?worker index required}"
    case "$index" in 0|1|2|3|4|5|6|7|8|9|10|11|12|13|14) ;; *) exit 2;; esac
    [ -f "$native/worker-$index/cluster.json" ]
    run_node "worker-$index" "10.89.90.$((30+index))" none worker --root /node
    ;;
  status) podman ps -a --filter label=org.dawl.cluster=v1 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  stop)
    for name in $(podman ps --filter label=org.dawl.cluster=v1 --format '{{.Names}}'); do
      [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.cluster" }}' "$name")" = v1 ]
      podman stop --time 30 "$name"
    done
    ;;
  *) echo 'Usage: cluster_containers.sh [build|seed|up|workers|status|stop]' >&2;exit 2;;
esac
