#!/bin/sh
# Run after the training pipeline, using an immutable reviewed release kit.
set -eu
root=/var/lib/dawl/seed-lab-001
kit="$1"
storage="$root/public-validation"
image=localhost/hypertrix-lab:004
finish() {
  code=$?
  if [ "$code" -ne 0 ]; then printf '{"status":"failed","exit_code":%s}\n' "$code" > "$root/public-finalization-status.json"; fi
  printf '{"finished":true,"exit_code":%s}\n' "$code" > "$root/public-lab-finished.json"
}
trap finish EXIT
[ ! -e "$root/public-finalization.json" ] || { echo 'Finalization evidence already exists'; exit 1; }
printf '{"status":"waiting-for-training"}\n' > "$root/public-finalization-status.json"
deadline=$(($(date +%s)+172800))
while [ ! -f "$root/lab-finished.json" ]; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo 'Training wait exceeded 48 hours'; exit 1; }
  sleep 20
done
python3 -c 'import json,sys;assert json.load(open(sys.argv[1]))["exit_code"]==0;assert json.load(open(sys.argv[2]))["status"]=="complete"' "$root/lab-finished.json" "$root/pipeline-status.json"
selected=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["selected_step"])' "$root/run/summary.json")
start=$((selected-250))
operator=$(cat "$root/run/operator.public.key")
printf '{"status":"publishing-selected-checkpoint"}\n' > "$root/public-finalization-status.json"
# This publisher includes the replay boundary, not just the selected model.
podman run --name hypertrix-seed4-public-publisher --network none --cpus 4 --memory 5g \
  -v "$kit:/work:ro" -v "$root:/experiment:ro" -v /var/lib/dawl/home-lab-rehearsal:/lab "$image" \
  -m dawl_lab.artifacts publish --root /lab/provider --dataset /experiment/dataset --run /experiment/run --step "$selected" > "$root/public-publication.json"
package=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["package_id"])' "$root/public-publication.json")
publisher=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["publisher"])' "$root/public-publication.json")
sh "$kit/lab/lab.sh" fetch "$kit" "$storage" final-auditor - no yes \
  --url http://127.80.0.11:8911 --package-id "$package" --publisher "$publisher" > "$root/public-transfer.json"
sh "$kit/lab/lab.sh" run "$kit" "$storage" final-auditor - no yes \
  -m dawl_lab.artifacts verify --root /lab/incoming --package-id "$package" --publisher "$publisher" > "$root/public-package-verification.json"
printf '{"status":"independent-public-gpu-replay"}\n' > "$root/public-finalization-status.json"
sh "$kit/lab/lab.sh" audit "$kit" "$storage" final-auditor - no no \
  --start "$start" --end "$selected" --expected-operator "$operator" > "$root/public-replay.log" 2>&1
podman run --name hypertrix-seed4-final-report --network none --cpus 2 --memory 2g \
  -v "$kit:/work:ro" -v "$root:/lab" "$image" /work/scripts/lab_report.py --root /lab
printf '{"status":"complete","public_package_verified":true,"isolated_gpu_replay_completed":true}\n' > "$root/public-finalization-status.json"
