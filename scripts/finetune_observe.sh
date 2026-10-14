#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/finetune-economics-001
if [ -f "$root/telemetry.pid" ] && kill -0 "$(cat "$root/telemetry.pid")" 2>/dev/null; then
  echo 'Fine-tuning telemetry is already running.'
else
  nohup python3 "$repo/scripts/local_finetune_telemetry.py" > "$root/telemetry.log" 2>&1 < /dev/null &
  echo "$!" > "$root/telemetry.pid"
fi
sh "$repo/scripts/local_testnet_console.sh" refresh
