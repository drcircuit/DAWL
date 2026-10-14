#!/bin/sh
# Continue the already-authorized bounded pilot as soon as the trainer exits.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/finetune-economics-001
while [ "$(podman inspect hypertrix-finetune-002 --format '{{.State.Running}}')" = true ]; do
  podman logs --tail 1 hypertrix-finetune-002 2>/dev/null
  sleep 30
done
[ "$(podman inspect hypertrix-finetune-002 --format '{{.State.ExitCode}}')" = 0 ] || { echo 'Trainer failed; preserving evidence.'; exit 1; }
[ -f "$root/training-summary.json" ] && [ -f "$root/control-result.json" ]
sh "$repo/scripts/finetune_replay.sh"
sh "$repo/scripts/finetune_pilot.sh" export
selected=$(python3 -c 'import json; print(json.load(open("/var/lib/dawl/finetune-economics-001/training-summary.json"))["selected_evaluation"]["selected"])')
case "$selected" in epoch-1|epoch-2|epoch-3) sh "$repo/scripts/finetune_convert.sh" "$selected" ;; *) echo 'Selection retained the baseline; no adapted model is being labelled as selected.' ;; esac
