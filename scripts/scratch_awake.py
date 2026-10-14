"""Temporary Windows idle-sleep guard; stops when this pilot finishes or its deadline expires."""
import ctypes
from datetime import datetime,timezone
import json
from pathlib import Path
import time

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    deadline=datetime(2026,10,15,4,0,tzinfo=timezone.utc).timestamp()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    keep=kernel.SetThreadExecutionState;keep.argtypes=[ctypes.c_uint32];keep.restype=ctypes.c_uint32
    if time.time()>=deadline:raise SystemExit('Overnight window ended')
    try:
        if not keep(0x80000001):raise ctypes.WinError(ctypes.get_last_error())
        with (a.root/'sleep-guard.jsonl').open('a') as f:f.write(json.dumps({'event':'active','deadline_unix':deadline,'scope':'System idle sleep only; display and power plan unchanged'})+'\n')
        while time.time()<deadline and not (a.root/'pilot-finished.json').exists():time.sleep(5)
    finally:
        keep(0x80000000)
        with (a.root/'sleep-guard.jsonl').open('a') as f:f.write(json.dumps({'event':'released'})+'\n')
