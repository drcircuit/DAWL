#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/seed-lab-001
image=localhost/hypertrix-lab:004
finish() {
  result=$?
  if [ "$result" -ne 0 ]; then printf '{"status":"failed","exit_code":%s}\n' "$result" > "$root/pipeline-status.json"; fi
  printf '{"pipeline_stopped":true,"exit_code":%s}\n' "$result" > "$root/lab-finished.json"
}
trap finish EXIT
# Calibration can overlap the independent data verifier. The long run is gated
# on both full source reconstruction and fresh-process numerical replay below.
[ -f "$root/dataset/blueprint.json" ]
runtime="$root/runtime-v1"
[ ! -e "$runtime" ] || { echo 'Frozen runtime exists; inspect its run before restarting'; exit 1; }
mkdir -p "$runtime"
cp -a "$repo/src" "$repo/scripts" "$repo/lab" "$runtime/"
mkdir -p "$runtime/research/corpora"
cp "$repo/research/corpora/seed-lab-quality-prompts.json" "$runtime/research/corpora/"
gpu() {
  name="$1"; folder="$2"; role="$3"; shift 3
  podman run --init --name "$name" --network none --cpus 8 --memory 16g --device /dev/dxg --shm-size 1g \
    --label org.hypertrix.seed=004 --label "org.hypertrix.role=$role" --label "org.hypertrix.work-root=$root/$folder" \
    -e PYTHONSAFEPATH=1 -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -e TORCHINDUCTOR_COMPILE_THREADS=4 \
    -e TORCHINDUCTOR_CACHE_DIR=/lab/compile-cache -e "HYPERTRIX_PROGRESS_ROOT=/lab/$folder" \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$runtime:/work:ro" -v "$root:/lab" "$image" "$@"
}
# A fresh compiled pilot tests the real model/data before spending a long run.
gpu hypertrix-seed4-probe-create probe model-trainer -m dawl_lab.run create --root /lab/probe --dataset /lab/dataset \
  --steps 20 --batch-size 96 --micro-batch 32 --checkpoint-every 10 --compile
gpu hypertrix-seed4-probe-first probe model-trainer -m dawl_lab.run train --root /lab/probe --dataset /lab/dataset --stop-step 10
gpu hypertrix-seed4-probe-resume probe model-trainer -m dawl_lab.run train --root /lab/probe --dataset /lab/dataset --resume
gpu hypertrix-seed4-probe-replay probe-verifier model-replay -m dawl_lab.run replay --root /lab/probe --dataset /lab/dataset --start 10 --end 20
gpu hypertrix-seed4-probe-verifier probe-verifier model-replay -m dawl_lab.verify run --root /lab/probe --dataset /lab/dataset --output /lab/probe/artifact-verification.json
# Fail-closed: the long run starts only after exact model AND optimizer replay.
python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["accepted"] and v["optimizer_exact"]' "$root/probe/replay-0000010-0000020.json"
code=$(podman wait hypertrix-seed4-data-verifier)
[ "$code" = 0 ] || { echo 'Data verification failed; long training was not started'; exit 1; }
python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["accepted"] and v["mode"]=="all-documents"' "$root/data-verification.json"
gpu hypertrix-seed4-create run model-trainer -m dawl_lab.run create --root /lab/run --dataset /lab/dataset \
  --steps 6000 --batch-size 96 --micro-batch 32 --checkpoint-every 250 --compile
gpu hypertrix-seed4-trainer run model-trainer -m dawl_lab.run train --root /lab/run --dataset /lab/dataset
selected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_step"])' "$root/run/summary.json")
start=$((selected-250))
gpu hypertrix-seed4-replay replay model-replay -m dawl_lab.run replay --root /lab/run --dataset /lab/dataset --start "$start" --end "$selected"
gpu hypertrix-seed4-verifier replay model-replay -m dawl_lab.verify run --root /lab/run --dataset /lab/dataset --output /lab/artifact-verification.json
podman run --name hypertrix-seed4-quality --network none --cpus 4 --memory 4g \
  -v "$runtime:/work:ro" -v "$root:/lab" "$image" /work/scripts/lab_quality.py \
  --root /lab/run --dataset /lab/dataset --corpus /lab/curated-v2 --step "$selected" \
  --prompts /work/research/corpora/seed-lab-quality-prompts.json --evaluation-sources /lab/curated-v2/sources.json --output /lab/quality-seed3.json
podman run --name hypertrix-seed4-publisher --network none --cpus 4 --memory 5g \
  -v "$runtime:/work:ro" -v "$root:/lab" "$image" -m dawl_lab.artifacts publish --root /lab/provider --dataset /lab/dataset --run /lab/run --step "$selected" > "$root/publication.json"
printf '{"status":"complete","training_and_replay_finished":true,"quality_comparison_recorded":true,"package_published":true}\n' > "$root/pipeline-status.json"
