#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/finetune-economics-001
image=localhost/hypertrix-finetune:002
case "${1:-status}" in
  acquire)
    [ ! -e "$root/source-lock.json" ] || { echo 'Experiment already prepared.' >&2; exit 1; }
    mkdir -p "$root/raw"
    for id in 3300 33310 4359; do
      rsync -4 --contimeout=15 --timeout=30 "rsync://gutenberg.pglaf.org/gutenberg-epub/$id/pg$id.txt" "$root/raw/pg$id.txt"
      rsync -4 --contimeout=15 --timeout=30 "rsync://gutenberg.pglaf.org/gutenberg-epub/$id/pg$id.rdf" "$root/raw/pg$id.rdf"
    done
    chown -R 1000:1000 "$root"
    podman run --rm --cap-drop ALL -v "$root:/pilot" -v "$repo:/repo:ro" --entrypoint python localhost/hypertrix-oracle-training:003 -P /repo/scripts/finetune_prepare.py
    ;;
  build)
    if podman image exists "$image"; then echo 'Image already exists; preserve it.' >&2; exit 1; fi
    podman build -f "$repo/Dockerfile.finetune" -t "$image" "$repo"
    ;;
  prepare)
    podman run --rm --cap-drop ALL -v "$root:/pilot" "$image" prepare
    ;;
  train)
    podman image inspect "$image" --format '{{.Id}}' > "$root/runtime-image-002-id.txt"
    remaining=$(python3 -c 'import json,time; print(max(1,int(json.load(open("/var/lib/dawl/finetune-economics-001/gpu-session.json"))["deadline_unix"]-time.time())))')
    podman run -d --name hypertrix-finetune-002 --label org.hypertrix.finetune=001 \
      --timeout "$remaining" --cap-drop ALL --device /dev/dxg --shm-size 1g \
      -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro \
      -v "$root:/pilot" --entrypoint sh "$image" \
      -c 'python -P -m dawl_finetune.run train --retry-failed-baseline && python -P -m dawl_finetune.run control'
    ;;
  export)
    podman run --rm --cap-drop ALL -v "$root:/pilot" "$image" export
    ;;
  status) podman ps -a --filter label=org.hypertrix.finetune=001 --format '{{.Names}} {{.Status}}' ;;
  *) echo 'Usage: acquire|build|prepare|train|export|status' >&2; exit 2 ;;
esac
