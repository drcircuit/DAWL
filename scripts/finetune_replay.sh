#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root="${1:-/var/lib/dawl/finetune-economics-001}"
image="${HYPERTRIX_FINETUNE_IMAGE:-localhost/hypertrix-finetune:004}"
[ -f "$root/training-summary.json" ] && [ -f "$root/control-result.json" ]
[ ! -e "$root/verifier-replay.py" ] || { echo 'Verifier source already pinned.'; exit 1; }
remaining=$(python3 -c 'import json,time,sys,pathlib; print(int(json.loads((pathlib.Path(sys.argv[1])/"gpu-session.json").read_text())["deadline_unix"]-time.time()))' "$root")
[ "$remaining" -gt 30 ] || { echo 'No remaining GPU session time.' >&2; exit 1; }
podman image exists "$image"
podman run --rm --entrypoint cat "$image" /app/scripts/replay_finetune.py > "$root/verifier-replay.py"
workspace=$(python3 -c 'import json,sys,pathlib; print(json.loads((pathlib.Path(sys.argv[1])/"recipe.json").read_text()).get("execution_policy",{}).get("cublas_workspace_config",""))' "$root")
set --
if [ -n "$workspace" ]; then set -- -e "CUBLAS_WORKSPACE_CONFIG=$workspace"; fi
podman run -d --name "hypertrix-replay-$(basename "$root")" --label org.hypertrix.finetune=001 \
  --label org.hypertrix.pilot="$root" \
  "$@" \
  --timeout "$remaining" --cap-drop ALL --device /dev/dxg --shm-size 1g \
  -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro \
  -v "$root:/pilot" --entrypoint python "$image" -P /pilot/verifier-replay.py
