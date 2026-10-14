#!/bin/sh
# Run once against the completed 003 pilot, before restoring its outage-test validator.
set -eu
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
native=/var/lib/dawl/oracle-training-003
evidence="$native/consumer-evidence"
consumer="$native/consumer"
[ ! -e "$evidence" ] && [ ! -e "$consumer" ] || { echo 'Fresh consumer directories required' >&2; exit 1; }
mkdir "$evidence" "$consumer"
for name in genesis.json training-certificate.json application-history.json stage.json manifest.json churn.json; do
  cp "$native/state/$name" "$evidence/"
done
chown -R 1000:1000 "$consumer" "$evidence"
podman stop --time 30 hypertrix-t4-003-provider hypertrix-t4-003-discovery hypertrix-t4-003-trainer-1 hypertrix-t4-003-trainer-2 hypertrix-t4-003-verifier-0
podman run --rm --init --name hypertrix-t4-003-cold-consumer --network host \
  --read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges --cpus 2 --memory 5g \
  -v "$evidence:/evidence:ro" -v "$consumer:/output" \
  -v "$repo/scripts/check_oracle_training_replica.py:/app/scripts/check_oracle_training_replica.py:ro" \
  -v "$repo/scripts/verify_oracle_training.py:/app/scripts/verify_oracle_training.py:ro" \
  --entrypoint python localhost/hypertrix-oracle-training:003 -P /app/scripts/check_oracle_training_replica.py \
  --evidence /evidence --root /output/replica --peer http://127.70.0.44:8900 \
  --genesis-sha256 5764519561f755af339ee6a0898dee7e2f8fe5966f489bccdc72b500959bb9a9
