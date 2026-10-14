#!/bin/sh
set -eu
root=/var/lib/dawl/finetune-economics-001
repo="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
converter=/var/lib/dawl/tools/llama-cpp-finetune-001
[ "$(git -C "$converter" rev-parse HEAD)" = 6868a52cb337450658006ad2f9281c2eb63f1666 ]
name="${1:-base}"
case "$name" in
  base) source=/pilot/base ;;
  epoch-1|epoch-2|epoch-3|synthetic-control)
    podman run --rm --cap-drop ALL -v "$root:/pilot" -v "$repo:/repo:ro" --entrypoint python \
      localhost/hypertrix-finetune:002 -P /repo/scripts/prepare_finetune_conversion.py --checkpoint "$name"
    source="/pilot/gguf-inputs/$name" ;;
  *) exit 2 ;;
esac
[ ! -e "$root/$name-bf16.gguf" ] || { echo 'GGUF already exists; preserving it.'; exit 1; }
podman run --rm --cap-drop ALL --cpus 4 -v "$root:/pilot" -v "$converter:/converter:ro" \
  localhost/hypertrix-gguf:001 "$source" --outfile "/pilot/$name-bf16.gguf" --outtype bf16
