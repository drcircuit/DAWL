#!/bin/sh
set -eu
action="$1"; bundle="$2"; storage="$3"; node="$4"; archive="$5"; install="$6"; cpu="$7"; shift 7
case "$storage" in /var/lib/dawl/*) ;; *) echo 'Native /var/lib/dawl storage required' >&2; exit 2;; esac
case "$storage" in *..*) exit 2;; esac
case "$node" in *[!a-z0-9-]*|'') echo 'Invalid node name' >&2; exit 2;; esac
if ! command -v podman >/dev/null 2>&1; then
  [ "$install" = yes ] || { echo 'Podman missing. Run Quickstart.ps1 with -Install once.' >&2; exit 2; }
  apt-get update
  apt-get install -y podman python3 ca-certificates
fi
mkdir -p "$storage"
chmod 750 "$storage"
# Verify and copy only listed release files into a native, immutable kit directory.
kit=$(python3 "$bundle/lab/install_release.py" "$bundle" "$storage")
image=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["runtime"]["image_id"])' "$bundle/release-manifest.json")
if ! podman image exists "$image"; then
  [ "$archive" != - ] || { echo 'Supply -ImageArchive with the matching runtime OCI archive.' >&2; exit 2; }
  podman load -i "$archive"
  podman image exists "$image" || { echo 'Imported runtime identity differs'; exit 2; }
fi
gpu_args=''
if [ "$cpu" != yes ]; then
  [ -e /dev/dxg ] || { echo 'WSL GPU passthrough is missing. Check the Windows NVIDIA driver, or use -Cpu.' >&2; exit 2; }
fi
prefix="hypertrix-home-$node"
network=none
case "$action" in ddp|fetch) network=host;; esac
execution=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
worker="$prefix-worker-$execution"
role=model-trainer
for item in "$@"; do
  case "$item" in replay) role=model-replay;; prepare|verify) role=tokenizer;; esac
done
run_cpu() {
  podman run --init --name "$worker" --network "$network" --cpus 8 --memory 12g -e PYTHONSAFEPATH=1 \
    --label org.hypertrix.seed=home --label "org.hypertrix.role=$role" --label "org.hypertrix.work-root=$storage/activities/$execution" \
    --label "org.hypertrix.execution-id=$execution" -e "HYPERTRIX_EXECUTION_ID=$execution" -e "HYPERTRIX_PROGRESS_ROOT=/lab/activities/$execution" \
    -v "$kit:/work:ro" -v "$storage:/lab" "$image" "$@"
}
run_gpu() {
  podman run --init --name "$worker" --network "$network" --cpus 8 --memory 16g --device /dev/dxg --shm-size 1g \
    --label org.hypertrix.seed=home --label "org.hypertrix.role=$role" --label "org.hypertrix.work-root=$storage/activities/$execution" \
    --label "org.hypertrix.execution-id=$execution" -e "HYPERTRIX_EXECUTION_ID=$execution" -e "HYPERTRIX_PROGRESS_ROOT=/lab/activities/$execution" \
    -e PYTHONSAFEPATH=1 -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -e TORCHINDUCTOR_COMPILE_THREADS=4 -e TORCHINDUCTOR_CACHE_DIR=/lab/compile-cache \
    -v /usr/lib/wsl:/usr/lib/wsl:ro -v "$kit:/work:ro" -v "$storage:/lab" "$image" "$@"
}
case "$action" in
audit)
  mkdir -p "$storage/audits/$execution"
  set -- "$image" /work/lab/replay.py --run /public/run --dataset /public/dataset --root /audit/result "$@"
  if [ "$cpu" != yes ]; then
    set -- --device /dev/dxg -e LD_LIBRARY_PATH=/usr/lib/wsl/lib -v /usr/lib/wsl:/usr/lib/wsl:ro "$@"
  else
    set -- "$@" --device cpu
  fi
  podman run --init --name "$worker" --network none --cpus 4 --memory 12g --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:size=256m --label org.hypertrix.seed=home --label org.hypertrix.role=model-replay \
    --label "org.hypertrix.work-root=$storage/audits/$execution/progress" --label "org.hypertrix.execution-id=$execution" \
    -e "HYPERTRIX_EXECUTION_ID=$execution" -e HYPERTRIX_PROGRESS_ROOT=/audit/progress \
    -e TORCHINDUCTOR_CACHE_DIR=/audit/compile-cache -e TRITON_CACHE_DIR=/audit/triton-cache -e CUDA_CACHE_PATH=/audit/cuda-cache \
    -e TORCHINDUCTOR_COMPILE_THREADS=4 -v "$kit:/work:ro" -v "$storage/incoming:/public:ro" -v "$storage/audits/$execution:/audit" "$@" ;;
network) run_cpu /work/lab/configure_network.py --output /lab/network.json "$@" ;;
fetch) run_cpu -m dawl_lab.artifacts pull --root /lab/incoming "$@" ;;
doctor)
  if [ "$cpu" = yes ]; then run_cpu /work/lab/doctor.py --output /lab/doctor.json --cpu; else run_gpu /work/lab/doctor.py --output /lab/doctor.json; fi ;;
run|ddp)
  [ "$#" -gt 0 ] || { echo 'Supply -Task with the Python module and arguments.'; exit 2; }
  if [ "$cpu" = yes ]; then run_cpu "$@"; else run_gpu "$@"; fi ;;
ddptest)
  run_cpu -m torch.distributed.run --standalone --nproc-per-node=2 -m dawl_lab.ddp --self-test --root /lab/ddp-self-test ;;
provider|mesh|studio)
  case "$action" in
    provider) module=dawl_lab.artifacts; role=cortex; folder=provider; set -- serve --root /lab/provider "$@" ;;
    mesh) module=dawl_lab.mesh; role=discovery; folder=peer; set -- --root /lab/peer --name "$node" "$@" ;;
    studio) module=dawl_lab.serve; role=model-host; folder=run; set -- --root /lab/run --dataset /lab/dataset "$@" ;;
  esac
  podman run -d --init --name "$prefix-$action" --network host --cpus 4 --memory 5g --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:size=128m -e PYTHONSAFEPATH=1 --label org.hypertrix.lab-role="$role" --label "org.hypertrix.work-root=$storage/$folder" \
    -v "$kit:/work:ro" -v "$storage:/lab" "$image" -m "$module" "$@" ;;
status) podman ps -a --filter "name=$prefix" --format '{{.Names}} {{.Status}}' ;;
stop)
  for hat in provider mesh studio; do if podman container exists "$prefix-$hat"; then podman stop "$prefix-$hat"; fi; done ;;
*) echo 'Unknown lab action' >&2; exit 2 ;;
esac
