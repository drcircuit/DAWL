#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
name=hypertrix-seed-playground-v3
case "${1:-status}" in
start)
  # Preserve the previous service container for rollback; only replace its listener.
  if podman container exists hypertrix-seed-playground; then podman stop hypertrix-seed-playground; fi
  if podman container exists "$name"; then podman start "$name"; else
    podman run -d --name "$name" --network host --read-only --cap-drop ALL --security-opt no-new-privileges \
      --cpus 4 --memory 4g --tmpfs /tmp:size=128m -e PYTHONPATH=/work/src \
      -v "$repo:/work:ro" -v /var/lib/dawl/scratch-001:/experiment:ro -v /var/lib/dawl/scratch-003:/expanded:ro \
      --entrypoint python localhost/hypertrix-finetune:005 -P /work/scripts/seed_playground.py \
      --root /experiment --dataset /experiment/dataset --html /work/scripts/scratch_playground.html
  fi
  ;;
stop) podman stop "$name" ;;
status) podman ps -a --filter "name=$name" --format '{{.Names}} {{.Status}}' ;;
*) exit 2 ;;
esac
