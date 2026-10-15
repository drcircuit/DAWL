#!/bin/sh
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root=/var/lib/dawl/seed-lab-001
image=localhost/hypertrix-lab:003
code=$(podman wait hypertrix-seed4-curator-v2)
[ "$code" = 0 ] || { echo 'Source acquisition failed; no dataset released'; exit 1; }
podman run --name hypertrix-seed4-curation-review-v3 --network none --cpus 4 --memory 8g \
  -v "$repo:/work:ro" -v "$root:/lab" "$image" /work/scripts/lab_review_corpus.py --intake /lab/corpus --root /lab/curated-v2
podman run --name hypertrix-seed4-tokenizer --network none --cpus 8 --memory 12g \
  --label org.hypertrix.seed=004 --label org.hypertrix.role=tokenizer --label "org.hypertrix.work-root=$root/dataset" \
  -v "$repo:/work:ro" -v "$root:/lab" "$image" -m dawl_lab.run prepare --root /lab/dataset --corpus /lab/curated-v2
podman run --name hypertrix-seed4-data-verifier --network none --cpus 6 --memory 8g \
  -e HYPERTRIX_PROGRESS_ROOT=/lab/data-verifier \
  --label org.hypertrix.seed=004 --label org.hypertrix.role=tokenizer --label "org.hypertrix.work-root=$root/data-verifier" \
  -v "$repo:/work:ro" -v "$root:/lab" "$image" -m dawl_lab.verify data --dataset /lab/dataset --corpus /lab/curated-v2 --full --output /lab/data-verification.json
