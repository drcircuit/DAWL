#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
producer="$(pwd)/artifacts/platform-v1"
auditor="$(pwd)/artifacts/platform-auditor-v1"
image=localhost/dawl-platform:0.1
network=dawl-platform-lab
storage=dawl-platform-artifacts
validator=dawl-platform-auditor
case "${1:-status}" in
  build) podman build -f Dockerfile.platform -t "$image" . ;;
  up)
    for name in "$storage" "$validator"; do
      if podman container exists "$name"; then echo "Container already exists: $name" >&2; exit 1; fi
    done
    if podman network exists "$network"; then
      [ "$(podman network inspect --format '{{ index .Labels "org.dawl.platform" }}' "$network")" = v1 ]
    else
      podman network create --disable-dns --label org.dawl.platform=v1 "$network"
    fi
    podman run -d --name "$storage" --label org.dawl.platform=v1 --network "$network" \
      -p 127.0.0.1:8890:8890 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
      --cpus 2 --memory 768m --pids-limit 128 \
      -v "$producer/objects:/node/objects:ro" -v "$producer/trust.json:/node/trust.json:ro" \
      -v "$producer/ledger.sqlite:/node/ledger.sqlite:ro" \
      "$image" serve --root /node --host 0.0.0.0 --port 8890 --allow-insecure-lab
    podman run -d --name "$validator" --label org.dawl.platform=v1 --network "$network" \
      -p 127.0.0.1:8891:8891 --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges \
      --cpus 2 --memory 2g --pids-limit 128 \
      -v "$auditor:/node" -v "$producer/identities/auditor/key.private.pem:/identity/key.private.pem:ro" \
      "$image" serve --root /node --host 0.0.0.0 --port 8891 --allow-insecure-lab
    ;;
  audit|sync)
    [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.platform" }}' "$validator")" = v1 ]
    [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.platform" }}' "$storage")" = v1 ]
    address="$(podman inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$storage")"
    [ -n "$address" ]
    if [ "$1" = audit ]; then
      podman exec "$validator" python dawl_platform.py audit --root /node --key /identity/key.private.pem \
        --peer "http://$address:8890" --allow-insecure-lab
    else
      podman exec "$validator" python dawl_platform.py sync --root /node --peer "http://$address:8890" --allow-insecure-lab
    fi
    ;;
  down)
    for name in "$validator" "$storage"; do
      if podman container exists "$name"; then
        [ "$(podman inspect --format '{{ index .Config.Labels "org.dawl.platform" }}' "$name")" = v1 ]
        podman stop --time 10 "$name"
        podman rm "$name"
      fi
    done
    if podman network exists "$network"; then
      [ "$(podman network inspect --format '{{ index .Labels "org.dawl.platform" }}' "$network")" = v1 ]
      podman network rm "$network"
    fi
    ;;
  status) podman ps -a --filter label=org.dawl.platform=v1 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' ;;
  *) echo "Usage: sh scripts/platform_containers.sh [build|up|audit|sync|down|status]" >&2; exit 2 ;;
esac
