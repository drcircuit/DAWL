#!/bin/sh
set -eu
root=/var/lib/dawl/seed-lab-001
kit=/var/lib/dawl/releases/hypertrix-home-lab-preview-003
podman run --name hypertrix-lab-publish-calibration --network none --cpus 4 --memory 5g \
  -v "$kit:/work:ro" -v "$root:/experiment:ro" -v /var/lib/dawl/home-lab-rehearsal:/lab localhost/hypertrix-lab:004 \
  -m dawl_lab.artifacts publish --root /lab/provider --dataset /experiment/dataset --run /experiment/probe --step 20 > "$root/calibration-publication.json"
