#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/finetune-repeatability-001
source=/var/lib/dawl/finetune-economics-001
image=localhost/hypertrix-finetune:002
[ ! -e "$root" ] || { echo 'Diagnostic already exists; preserve it.' >&2; exit 1; }
mkdir "$root"
cp "$repo/scripts/finetune_repeatability.py" "$root/finetune_repeatability.py"
cp "$repo/src/dawl_finetune/execution.py" "$root/execution.py"
cp "$source/operator.public.key" "$root/operator.public.key"
podman image inspect "$image" --format '{{.Id}}' > "$root/runtime-image-id.txt"
chown -R 1000:1000 "$root"
podman run -d --name hypertrix-finetune-replay-determinism-001 --label org.hypertrix.finetune=001 \
  --label org.hypertrix.pilot="$root" --timeout 1230 --cap-drop ALL --device /dev/dxg --shm-size 1g \
  -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro \
  -v "$source:/source:ro" -v "$root:/experiment" --entrypoint python "$image" \
  -P /experiment/finetune_repeatability.py matrix
