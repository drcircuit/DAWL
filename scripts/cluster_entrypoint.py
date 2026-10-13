"""Supervise each validator's private ABCI adapter, application and CometBFT process."""
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

if len(sys.argv)<2 or sys.argv[1]!="validator":
    raise SystemExit(subprocess.call([sys.executable,"/app/dawl_cluster.py",*sys.argv[1:]]))
root=Path(sys.argv[2] if len(sys.argv)>2 else "/node")
children=[]
stopping=False
def stop(signum=None,frame=None):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
code=0
try:
    children.append(subprocess.Popen([sys.executable,"/app/dawl_cluster.py","abci","--root",str(root),"--port","26659"]))
    for _ in range(100):
        if children[0].poll() is not None: raise RuntimeError("ABCI application failed to start")
        try:
            with urlopen("http://127.0.0.1:26659/health",timeout=1): break
        except OSError: time.sleep(.1)
    else: raise RuntimeError("ABCI application readiness timeout")
    children.append(subprocess.Popen(["dawl-consensus"]))
    children.append(subprocess.Popen(["cometbft","node","--home",str(root/"comet")]))
    while not stopping:
        if any(p.poll() is not None for p in children):
            code=1;break
        time.sleep(.2)
finally:
    for process in reversed(children):
        if process.poll() is None: process.terminate()
    for process in reversed(children):
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait()
raise SystemExit(code)
