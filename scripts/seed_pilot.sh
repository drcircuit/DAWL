#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-003
image=localhost/hypertrix-finetune:005
remaining() { python3 -c 'import time; print(max(0,int(1792036800-time.time())))'; }
[ "$(remaining)" -gt 300 ] || { echo 'Overnight allowance ended'; exit 1; }
mkdir -p "$root"
chown 1000:1000 "$root"
run() {
  name="$1"; role="$2"; work="$3"; shift 3
  podman run --init --name "$name" --network none --cap-drop ALL --security-opt no-new-privileges \
    --cpus 6 --memory 12g --timeout "$(remaining)" --device /dev/dxg --shm-size 1g \
    --label org.hypertrix.seed=003 --label "org.hypertrix.role=$role" --label "org.hypertrix.work-root=$root/$work" \
    -e PYTHONPATH=/work/src -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -e CUBLAS_WORKSPACE_CONFIG=:4096:8 \
    -e TOKENIZERS_PARALLELISM=false -e "HYPERTRIX_PROGRESS_ROOT=/experiment/$work" \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$repo:/work:ro" -v "$root:/experiment" \
    -v /var/lib/dawl/scratch-001:/previous:ro -v /var/lib/dawl/seed-books-003:/intake:ro \
    --entrypoint python "$image" -P "$@"
}
case "${1:-prepare}" in
prepare) run hypertrix-seed3-tokenizer-v2 tokenizer dataset -m dawl_seed.run prepare --root /experiment/dataset --books /previous/dataset --code /intake ;;
benchmark) run hypertrix-seed3-benchmark model-trainer benchmark /work/scripts/seed_benchmark.py --root /experiment/benchmark --dataset /experiment/dataset ;;
probe) run hypertrix-seed3-probe model-trainer probe -m dawl_seed.run train --root /experiment/probe --dataset /experiment/dataset --epochs 1 --max-steps 30 --batch-size "${SEED_BATCH:-64}" --precision "${SEED_PRECISION:-bf16}" --attention "${SEED_ATTENTION:-flash}" --budget-seconds 600 --checkpoint-every 15 ;;
verify-data) run hypertrix-seed3-data-replay tokenizer data-replay /work/scripts/verify_seed_data.py --root /experiment/dataset --parent /previous/dataset --intake /intake ;;
probe-replay) run hypertrix-seed3-probe-replay model-replay probe-replay -m dawl_seed.run replay --root /experiment/probe --dataset /experiment/dataset --start 0 --end 30 ;;
compare) run hypertrix-seed3-comparison model-replay comparison /work/scripts/compare_seed_models.py --root /experiment --previous /previous ;;
train) run hypertrix-seed3-trainer model-trainer dense -m dawl_seed.run train --root /experiment/dense --dataset /experiment/dataset --epochs "${SEED_EPOCHS:-4}" --batch-size "${SEED_BATCH:-64}" --precision "${SEED_PRECISION:-bf16}" --attention "${SEED_ATTENTION:-flash}" --budget-seconds "${SEED_SECONDS:-9000}" --checkpoint-every 250 ;;
replay) start="${2:-0}"; end="${3:-250}"; run "hypertrix-seed3-replay-$start-$end" model-replay "replay-$start-$end" -m dawl_seed.run replay --root /experiment/dense --dataset /experiment/dataset --start "$start" --end "$end" ;;
*) echo 'Usage: prepare|benchmark|train|replay [start end]' >&2; exit 2 ;;
esac
