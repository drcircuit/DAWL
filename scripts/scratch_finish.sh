#!/bin/sh
# Continue this already-started overnight pilot; stop immediately on a failed stage.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/scratch-001
finish() {
  result=$?
  python3 - "$root" "$result" <<'PY'
import datetime,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
result={'exit_code':int(sys.argv[2]),'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
(root/'pilot-finished.json').write_text(json.dumps(result))
PY
}
trap finish EXIT
[ "$(podman wait hypertrix-seed-dense)" = 0 ]
sh "$repo/scripts/scratch_pilot.sh" replay-dense
sh "$repo/scripts/scratch_pilot.sh" moe
sh "$repo/scripts/scratch_pilot.sh" replay-moe
python3 "$repo/scripts/report_scratch.py" --root "$root" --output "$repo/docs/results/scratch-001"
