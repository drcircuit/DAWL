#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-001
name=hypertrix-seed-playground
case "${1:-status}" in
start)
  if podman container exists "$name"; then
    podman start "$name"
  else
    podman run -d --name "$name" --network host --read-only --cap-drop ALL --security-opt no-new-privileges \
      --cpus 4 --memory 3g --tmpfs /tmp:size=128m -e PYTHONPATH=/work/src \
      -v "$repo:/work:ro" -v "$root:/experiment:ro" --entrypoint python localhost/hypertrix-finetune:005 \
      -P -m dawl_scratch.serve --root /experiment --dataset /experiment/dataset --html /work/scripts/scratch_playground.html
  fi
  ;;
stop) podman stop "$name" ;;
status) podman ps -a --filter "name=$name" --format '{{.Names}} {{.Status}}' ;;
*) exit 2 ;;
esac
