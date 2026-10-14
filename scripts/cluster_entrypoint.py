"""Supervise each validator's private ABCI adapter, application and CometBFT process."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import json
import ipaddress
from urllib.request import urlopen

if len(sys.argv)<2 or sys.argv[1]!="validator":
    os.execv(sys.executable,[sys.executable,"/app/dawl_cluster.py",*sys.argv[1:]])
root=Path(sys.argv[2] if len(sys.argv)>2 else "/node")
config=json.loads((root/"cluster.json").read_text())
abci_host=config.get("abci_host","127.0.0.1")
if not ipaddress.IPv4Address(abci_host).is_loopback:
    raise ValueError("ABCI must remain on loopback")
children=[]
stopping=False
def stop(signum=None,frame=None):
    global stopping
    stopping=True
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
code=0
try:
    unix=config.get('abci_unix',False)
    sockets=root/'sockets'
    if unix:
        sockets.mkdir(mode=0o700,exist_ok=True)
        os.chmod(sockets,0o700)
    application_socket=sockets/'application.sock'
    app_args=['--uds',str(application_socket)] if unix else ['--host',abci_host,'--port','26659']
    children.append(subprocess.Popen([sys.executable,"/app/dawl_cluster.py","abci","--root",str(root),*app_args]))
    for _ in range(100):
        if children[0].poll() is not None: raise RuntimeError("ABCI application failed to start")
        try:
            if unix:
                import httpx
                with httpx.Client(transport=httpx.HTTPTransport(uds=str(application_socket)),timeout=1) as client:
                    client.get('http://localhost/health').raise_for_status()
                break
            with urlopen(f"http://{abci_host}:26659/health",timeout=1): break
        except OSError: time.sleep(.1)
        except Exception as error:
            if not unix: raise
            time.sleep(.1)
    else: raise RuntimeError("ABCI application readiness timeout")
    adapter_args=['--listen','unix://'+str(sockets/'abci.sock'),'--application-socket',str(application_socket)] if unix else [
        '--listen',f'tcp://{abci_host}:26658','--application',f'http://{abci_host}:26659']
    children.append(subprocess.Popen(["dawl-consensus",*adapter_args]))
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
