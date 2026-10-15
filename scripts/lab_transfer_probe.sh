#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/seed-lab-001
kit=/var/lib/dawl/releases/hypertrix-home-lab-preview-003
storage=/var/lib/dawl/home-lab-peer-b
package=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["package_id"])' "$root/calibration-publication.json")
publisher=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["publisher"])' "$root/calibration-publication.json")
sh "$kit/lab/lab.sh" fetch "$kit" "$storage" peer-b - no yes \
  --url http://127.80.0.11:8911 --package-id "$package" --publisher "$publisher" > "$root/calibration-transfer.json"
sh "$kit/lab/lab.sh" run "$kit" "$storage" peer-b - no yes \
  -m dawl_lab.artifacts verify --root /lab/incoming --package-id "$package" --publisher "$publisher" > "$root/calibration-package-verification.json"
