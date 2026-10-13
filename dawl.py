"""Run the Stage 1 experiment directly from a checkout, without installation."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from data_ingestion.experiment import DEFAULT_TEXT, run_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Strict UTF-8 source file; default is a built-in fixture")
    parser.add_argument("--source-id", help="Source identifier recorded in the manifest")
    parser.add_argument("--samples", type=int, default=64, help="Distinct positions per challenge")
    parser.add_argument("--repeats", type=int, default=5, help="Complete local audit repetitions")
    parser.add_argument("--output", type=Path, help="Publish a new block directory and JSON metrics here")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    try:
        # read_bytes preserves CRLF: universal-newline translation would alter source hashes.
        text = args.input.read_bytes().decode("utf-8", errors="strict") if args.input else DEFAULT_TEXT
        source_id = args.source_id or (args.input.name if args.input else "fixture:dawl-stage-1")
        report = run_experiment(text, source_id, args.samples, args.repeats, args.output)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(2, f"dawl: {error}\n")
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
