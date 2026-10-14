"""Process-scoped Windows idle-sleep guard for one bounded local GPU pilot."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

p=argparse.ArgumentParser();p.add_argument('--pilot',type=Path,required=True);p.add_argument('--log',type=Path,required=True)
a=p.parse_args();deadline=json.loads((a.pilot/'gpu-session.json').read_text())['deadline_unix']
if os.name!='nt':raise RuntimeError('This guard uses the Windows power-management API')
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
keep_awake=kernel.SetThreadExecutionState;keep_awake.argtypes=[ctypes.c_uint32];keep_awake.restype=ctypes.c_uint32
CONTINUOUS=0x80000000;SYSTEM_REQUIRED=1;DISPLAY_REQUIRED=2
def record(event,**details):
    with a.log.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'event':event,'pid':os.getpid(),**details})+'\n')
if time.time()>=deadline:raise RuntimeError('GPU pilot deadline already passed')
try:
    result=keep_awake(CONTINUOUS|SYSTEM_REQUIRED|DISPLAY_REQUIRED)
    if not result:raise ctypes.WinError(ctypes.get_last_error())
    record('idle-sleep-guard-active',deadline_unix=deadline,system_required=True,display_required=True,
           scope='Temporary thread execution state; no power-plan, lid, lock, or explicit-sleep policy changed.')
    while time.time()<deadline:
        if (a.pilot/'replay-verification.json').exists():break
        time.sleep(min(5,max(.1,deadline-time.time())))
finally:
    keep_awake(CONTINUOUS)
    record('idle-sleep-guard-released')
