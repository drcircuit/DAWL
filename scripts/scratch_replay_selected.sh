#!/bin/sh
# Replay at least 100 updates into each selected checkpoint, restoring AdamW state.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-001
deadline=1792036800
for model in dense moe; do
  interval=$(python3 - "$root/$model" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
end=json.loads((root/'summary.json').read_bytes())['selected_step']
steps=[int(p.name) for p in (root/'checkpoints').iterdir() if (p/'checkpoint.json').exists()]
eligible=[s for s in steps if end-s>=100]
if not eligible:raise SystemExit('No selected interval of at least 100 updates')
print(max(eligible),end)
PY
  )
  # These are two integers produced from our checkpoint metadata, not shell code.
  set -- $interval
  remaining=$(python3 -c 'import time,sys; print(max(0,int(int(sys.argv[1])-time.time())))' "$deadline")
  [ "$remaining" -ge 300 ] || { echo 'Overnight window ended'; exit 1; }
  podman run --name "hypertrix-seed-$model-selected-replay" --network none --cap-drop ALL --cpus 4 --memory 8g \
    --timeout "$remaining" --device /dev/dxg --shm-size 1g \
    -e PYTHONPATH=/work/src -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -e CUBLAS_WORKSPACE_CONFIG=:4096:8 \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$repo:/work:ro" -v "$root:/experiment" \
    --entrypoint python localhost/hypertrix-finetune:005 -P -m dawl_scratch.run replay \
    --root "/experiment/$model" --dataset /experiment/dataset --start "$1" --end "$2"
done
python3 "$repo/scripts/report_scratch.py" --root "$root" --output "$repo/docs/results/scratch-001"
