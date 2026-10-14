#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-003
finish() {
  result=$?
  python3 - "$root/pilot-finished.json" "$result" <<'PY'
import json,sys,time
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'exit_code':int(sys.argv[2]),'finished_unix':time.time()}))
PY
}
trap finish EXIT
# Require the actual fresh-process numerical replay and data reconstruction.
python3 - "$root" <<'PY'
import json,sys
from pathlib import Path
r=Path(sys.argv[1])
p=json.loads((r/'probe/replay-0000000-0000030.json').read_bytes())
if not p['accepted'] or p['exact_tensors']!=p['tensor_count'] or not p['optimizer_exact']:raise SystemExit('Probe replay rejected')
if not (r/'dataset/data-verification.json').exists():raise SystemExit('Data replay receipt missing')
PY
sh "$repo/scripts/seed_pilot.sh" train
sh "$repo/scripts/seed_pilot.sh" replay 0 250
interval="$(python3 - "$root/dense" <<'PY'
import json,sys
from pathlib import Path
r=Path(sys.argv[1]);selected=json.loads((r/'summary.json').read_bytes())['selected_step']
steps=sorted(int(p.name) for p in (r/'checkpoints').iterdir() if (p/'checkpoint.json').exists())
candidates=[s for s in steps if s<selected and selected-s>=100]
if not candidates:raise SystemExit('Selected checkpoint has insufficient replay interval')
print(max(candidates),selected)
PY
)"
set -- $interval
if [ "$1 $2" != '0 250' ]; then sh "$repo/scripts/seed_pilot.sh" replay "$1" "$2"; fi
sh "$repo/scripts/seed_pilot.sh" compare
