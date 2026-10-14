#!/bin/sh
# New lineage and bounded full-epoch replay; never mutate the original pilot.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/finetune-strict-002
source=/var/lib/dawl/finetune-economics-001
image=localhost/hypertrix-finetune:005
deadline=1792006200
# The timestamp is 2026-10-14 21:30 Europe/Oslo (19:30 UTC).
remaining() { python3 -c 'import time,sys; print(max(0,int(int(sys.argv[1])-time.time())))' "$deadline"; }
[ "$(remaining)" -gt 600 ] || { echo 'Insufficient time before approved deadline'; exit 1; }
[ ! -e "$root" ] || { echo 'Experiment exists; inspect rather than replace'; exit 1; }
mkdir "$root"
cp --reflink=auto -r "$source/base" "$source/raw" "$root/"
cp "$source/source-lock.json" "$source/base-lock.json" "$root/"
cp "$repo/scripts/finetune_strict_pilot.sh" "$root/orchestrator.sh"
chown -R 1000:1000 "$root"
podman image exists "$image" || podman build -f "$repo/Dockerfile.finetune-exact" -t "$image" "$repo"
podman image inspect "$image" --format '{{.Id}}' > "$root/runtime-image-id.txt"
podman run --rm --timeout "$(remaining)" --cap-drop ALL -v "$root:/pilot" "$image" prepare --execution-policy strict-math --epochs 1
podman run --rm --entrypoint cat "$image" /app/scripts/replay_finetune.py > "$root/verifier-replay.py"
chown 1000:1000 "$root/verifier-replay.py" "$root/runtime-image-id.txt"
podman run -d --name hypertrix-finetune-strict-002 --label org.hypertrix.finetune=001 --label org.hypertrix.pilot="$root" \
  --timeout "$(remaining)" --cap-drop ALL --device /dev/dxg --shm-size 1g \
  -e CUBLAS_WORKSPACE_CONFIG=:4096:8 -e LD_LIBRARY_PATH=/usr/lib/wsl/lib \
  -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$root:/pilot" "$image" train
code=$(podman wait hypertrix-finetune-strict-002)
[ "$code" = 0 ] || { echo "Trainer failed: $code; artifacts preserved"; exit 1; }
[ "$(remaining)" -gt 120 ] || { echo 'Deadline reached before replay'; exit 1; }
podman run -d --name hypertrix-replay-strict-002 --label org.hypertrix.finetune=001 --label org.hypertrix.pilot="$root" \
  --timeout "$(remaining)" --cap-drop ALL --device /dev/dxg --shm-size 1g \
  -e CUBLAS_WORKSPACE_CONFIG=:4096:8 -e LD_LIBRARY_PATH=/usr/lib/wsl/lib \
  -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$root:/pilot" --entrypoint python "$image" -P /pilot/verifier-replay.py
code=$(podman wait hypertrix-replay-strict-002)
[ "$code" = 0 ] || { echo "Replay exited: $code; artifacts preserved"; exit 1; }
[ "$(remaining)" -gt 120 ] || { echo 'Deadline reached before CPU export'; exit 1; }
podman run --name hypertrix-export-strict-002 --label org.hypertrix.finetune=001 --label org.hypertrix.pilot="$root" \
  --timeout "$(remaining)" --cap-drop ALL -v "$root:/pilot" "$image" export
echo 'Strict full-epoch experiment completed; inspect replay-verification.json before claiming success.'
