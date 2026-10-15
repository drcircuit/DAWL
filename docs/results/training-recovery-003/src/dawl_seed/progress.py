"""Atomic operational progress, separate from signed experimental evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def progress(root, phase, **values):
    root = Path(os.environ.get('HYPERTRIX_PROGRESS_ROOT') or root)
    root.mkdir(parents=True, exist_ok=True)
    value = {'schema': 'hypertrix.work-progress/1', 'observed_at': datetime.now(timezone.utc).isoformat(),
             'phase': phase, 'execution_id': os.environ.get('HOSTNAME'), **values}
    pending = root/'progress.pending'
    pending.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
    pending.replace(root/'progress.json')


def remaining():
    # Authorized overnight pilot: 15 October 2026, 06:00 Europe/Oslo.
    return max(0, 1792036800-time.time())
