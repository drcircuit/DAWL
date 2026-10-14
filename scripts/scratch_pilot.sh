#!/bin/sh
# Bounded experiment; all source code is inert input and containers have no network.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-001
code=/mnt/c/Users/espen/.cache/hypertrix/github-drcircuit-001
image=localhost/hypertrix-finetune:005
# User authorized the remainder of the night; hard stop 2026-10-15 06:00 Oslo.
deadline=1792036800
remaining() { python3 -c 'import time,sys; print(max(0,int(int(sys.argv[1])-time.time())))' "$deadline"; }
[ "$(remaining)" -gt 600 ] || { echo 'Overnight window ended'; exit 1; }
[ -f "$code/corpus-lock.json" ] || { echo 'Repository snapshot must complete first'; exit 1; }
mkdir -p "$root"
chown 1000:1000 "$root"
if [ ! -f "$root/dataset/blueprint.json" ]; then
  podman run --rm --name hypertrix-seed-prepare --network none --cap-drop ALL --cpus 4 --memory 6g \
    --timeout 900 -e PYTHONPATH=/work/src -e TOKENIZERS_PARALLELISM=false \
    -v "$repo:/work:ro" -v "$root:/experiment" -v "$code:/source-code:ro" \
    -v /var/lib/dawl/finetune-strict-002:/books:ro --entrypoint python "$image" \
    -P -m dawl_scratch.run prepare --root /experiment/dataset --books /books --code /source-code
fi
gpu_run() {
  name="$1"; shift
  podman run --name "$name" --network none --cap-drop ALL --cpus 4 --memory 8g \
    --timeout "$(remaining)" --device /dev/dxg --shm-size 1g \
    -e PYTHONPATH=/work/src -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -e CUBLAS_WORKSPACE_CONFIG=:4096:8 \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$repo:/work:ro" -v "$root:/experiment" \
    --entrypoint python "$image" -P -m dawl_scratch.run "$@"
}
case "${1:-dense}" in
prepare) ;;
dense)
  gpu_run hypertrix-seed-dense train --root /experiment/dense --dataset /experiment/dataset \
    --epochs 2 --batch-size 16 --budget-seconds 5400 --checkpoint-every 250
  ;;
replay-dense)
  gpu_run hypertrix-seed-dense-replay replay --root /experiment/dense --dataset /experiment/dataset --start 0 --end 250
  ;;
moe)
  gpu_run hypertrix-seed-moe train --root /experiment/moe --dataset /experiment/dataset \
    --parent /experiment/dense --epochs 1 --batch-size 16 --learning-rate .0002 --budget-seconds 1800 --checkpoint-every 100
  ;;
replay-moe)
  gpu_run hypertrix-seed-moe-replay replay --root /experiment/moe --dataset /experiment/dataset --start 0 --end 100
  ;;
*) echo 'Usage: prepare|dense|replay-dense|moe|replay-moe' >&2; exit 2 ;;
esac
