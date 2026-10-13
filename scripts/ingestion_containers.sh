#!/bin/sh
# Run from Linux/WSL with Podman installed. No training or GPU devices.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
run_dir="$(pwd)/artifacts/ingestion-demo"
image=localhost/dawl-ingestion:0.2
network=dawl-ingestion-pilot
storage=dawl-ingestion-storage
validator=dawl-ingestion-validator
label=org.dawl.pilot

owned_container() {
    [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.pilot" }}' "$1")" = ingestion-v2 ]
}

case "${1:-status}" in
  build)
    podman build -t "$image" .
    ;;
  up)
    for name in "$storage" "$validator"; do
        if podman container exists "$name"; then
            echo "Container $name exists; inspect it or run this script with down first." >&2
            exit 1
        fi
    done
    # Generated locally by prepare_ingestion_demo.py; never supplied by a peer.
    . "$run_dir/deploy.env"
    if podman network exists "$network"; then
        [ "$(podman network inspect --format '{{ index .Labels "org.dawl.pilot" }}' "$network")" = ingestion-v2 ]
    else
        podman network create --disable-dns --label "$label=ingestion-v2" "$network"
    fi
    podman run -d --name "$storage" --label "$label=ingestion-v2" \
      --network "$network" -p 127.0.0.1:8765:8765 \
      --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
      --memory 768m --cpus 2 --pids-limit 128 \
      -v "$run_dir/miner:/store:ro" \
      -v "$run_dir/miner-identity/api.token:/identity/api.token:ro" \
      "$image" serve-storage --store /store --token-file /identity/api.token --host 0.0.0.0
    storage_ip="$(podman inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$storage")"
    [ -n "$storage_ip" ]
    podman run -d --name "$validator" --label "$label=ingestion-v2" \
      --network "$network" -p 127.0.0.1:8766:8766 \
      --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
      --memory 768m --cpus 2 --pids-limit 128 \
      -v "$run_dir/trust:/trust:ro" \
      -v "$run_dir/miner-identity/api.token:/peer/api.token:ro" \
      -v "$run_dir/validator-identity:/identity:ro" \
      -v "$run_dir/receipts:/receipts" \
      "$image" serve-validator --peer "http://$storage_ip:8765" --allow-insecure-lab \
      --peer-token-file /peer/api.token --token-file /identity/api.token --key /identity/key.private.pem \
      --registry /trust/source.json --registry-id "$DAWL_REGISTRY_ID" \
      --tokenizer /trust/tokenizer.json --tokenizer-sha256 "$DAWL_TOKENIZER_SHA256" \
      --miner-public-key /trust/miner-public.key --receipts /receipts --host 0.0.0.0
    ;;
  down)
    # Remove only this pilot's labelled containers, preserving all mounted data.
    for name in "$validator" "$storage"; do
        if podman container exists "$name"; then
            owned_container "$name"
            podman stop --time 10 "$name"
            podman rm "$name"
        fi
    done
    if podman network exists "$network"; then
        [ "$(podman network inspect --format '{{ index .Labels "org.dawl.pilot" }}' "$network")" = ingestion-v2 ]
        podman network rm "$network"
    fi
    ;;
  status)
    podman ps -a --filter "label=$label=ingestion-v2" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  *) echo "Usage: sh scripts/ingestion_containers.sh [build|up|status|down]" >&2; exit 2 ;;
esac
