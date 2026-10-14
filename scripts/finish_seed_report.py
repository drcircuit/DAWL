"""Complete the bounded overnight report after training, both replays and evaluation."""
import argparse
import ctypes
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from report_seed import report
from write_seed_results import write_results
from seal_seed_release import seal


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--intake',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--deadline',required=True);a=p.parse_args()
    deadline=datetime.fromisoformat(a.deadline.replace('Z','+00:00')).timestamp()
    if a.output.exists():raise ValueError('Use a new report directory; preserve existing reports')
    keep=None;status={'status':'waiting','deadline':a.deadline}
    (a.root/'report-status.json').write_text(json.dumps(status,indent=2))
    try:
        if sys.platform=='win32':
            keep=ctypes.WinDLL('kernel32',use_last_error=True).SetThreadExecutionState
            keep.argtypes=[ctypes.c_uint32];keep.restype=ctypes.c_uint32
            if not keep(0x80000001):raise ctypes.WinError(ctypes.get_last_error())
        while time.time()<deadline:
            marker=a.root/'pilot-finished.json'
            if marker.exists():
                if json.loads(marker.read_bytes())['exit_code']!=0:raise ValueError('Training pipeline did not complete successfully')
                break
            time.sleep(min(15,max(0,deadline-time.time())))
        else:raise TimeoutError('Overnight reporting deadline reached')
        status['status']='checking';(a.root/'report-status.json').write_text(json.dumps(status))
        report(a.root,a.intake,a.output)
        subprocess.run([sys.executable,str(Path(__file__).parent/'plot_seed.py'),'--release',str(a.output)],check=True,timeout=120)
        write_results(a.output)
        result=seal(a.output,a.root/'dense/operator.private.pem')
        status={'status':'complete','release':str(a.output),'verification':result,'finished_utc':datetime.now(timezone.utc).isoformat()}
    except Exception as error:
        status={'status':'failed','reason':str(error),'finished_utc':datetime.now(timezone.utc).isoformat()}
        raise
    finally:
        if keep:keep(0x80000000)
        (a.root/'report-status.json').write_text(json.dumps(status,indent=2))
        print(json.dumps(status),flush=True)


if __name__=='__main__':main()
