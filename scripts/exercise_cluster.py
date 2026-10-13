"""Exercise real quorum loss, complete corpus work, fenced leases and release finality."""
import asyncio
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from dawl_node.common import canonical_json,load_key,read_json,sign,write_json
from dawl_cluster.client import Client
from dawl_cluster.state import summary

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root",type=Path,default=ROOT/"artifacts/cluster-v2")
parser.add_argument("--output",type=Path,default=ROOT/"artifacts/cluster-run-002")
args=parser.parse_args()
CLUSTER=args.root.resolve()
OUT=args.output.resolve()

def podman(*args):
    return subprocess.run(["wsl","-d","kali-linux","-u","root","--exec","podman",*args],
                          check=True,capture_output=True,text=True,timeout=50).stdout.strip()

async def control(*args):
    return await asyncio.to_thread(podman,*args)

async def wait_for(action, predicate, timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            value=await action()
            if predicate(value): return value
        except (OSError,ValueError): pass
        await asyncio.sleep(.7)
    raise TimeoutError("cluster condition did not arrive")

async def main():
    OUT.mkdir(exist_ok=False)
    client=Client(CLUSTER/"publisher",load_key(CLUSTER/"publisher/key.private.pem"))
    started=time.perf_counter();report={"started_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"phases":[]}
    def record(name,value):
        write_json(OUT/(name+".json"),value)
        report["phases"].append({"name":name,"seconds":round(time.perf_counter()-started,3)})
        print(name,flush=True)
    worker_key=load_key(CLUSTER/"worker-0/key.private.pem")
    public=worker_key.public_key().public_bytes_raw().hex()
    try:
        initial=await client.state()
        assert initial["actors"][public]["sequence"]==0
        record("01-healthy",summary(initial))
        await control("stop","--time","20","dawl-cluster-validator-0")
        state=await wait_for(lambda:client.state("http://127.0.0.1:8922"),lambda s:s["height"]>=initial["height"]+4)
        record("02-three-validators-progress",summary(state))
        await control("stop","--time","20","dawl-cluster-validator-1")
        await asyncio.sleep(3)
        halted=await client.state("http://127.0.0.1:8922")
        tx=sign({"schema":"dawl.cluster-tx/1","chain_id":halted["chain_id"],"plan_sha256":halted["plan_sha256"],
                 "sequence":1,"valid_until":halted["height"]+300,"kind":"claim","payload":{"capability":"AUDIT_CPU"}},worker_key)
        broadcast=await client.rpc("broadcast_tx_sync",{"tx":base64.b64encode(canonical_json(tx)).decode()},"http://127.0.0.1:8922")
        assert broadcast["code"]==0
        await asyncio.sleep(8)
        still=await client.state("http://127.0.0.1:8922")
        assert still["height"]==halted["height"] and still["actors"][public]["sequence"]==0
        record("03-two-validators-halt",{"height_before":halted["height"],"height_after":still["height"],
                                       "checktx_accepted":True,"transaction_committed":False,"wait_seconds":8,"probe":tx})
        await control("start","dawl-cluster-validator-1")
        committed=await wait_for(lambda:client.state("http://127.0.0.1:8922"),lambda s:s["actors"][public]["last_tx"]==tx["id"])
        lease=committed["actors"][public]["last_result"]
        record("04-third-validator-restores-commit",{"height":committed["height"],"lease":lease,"transaction":tx["id"]})
        await control("start","dawl-cluster-validator-0")
        # Worker zero deliberately stays absent after its committed claim; the lease must expire.
        for index in (1,2):
            await asyncio.to_thread(subprocess.run,["wsl","-d","kali-linux","-u","root","--exec","sh",
                str(ROOT/"scripts/cluster_containers.sh").replace("C:\\","/mnt/c/").replace("\\","/"),"worker",str(index)],
                check=True,capture_output=True,timeout=40)
        out=open(OUT/"host-worker.log","w",encoding="utf-8")
        process=subprocess.Popen([sys.executable,str(ROOT/"dawl_cluster.py"),"worker","--root",str(CLUSTER/"worker-3")],
                                 cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        write_json(OUT/"host-worker-process.json",{"pid":process.pid,"role":"worker-3","command":"dawl_cluster.py worker"})
        deadline=time.monotonic()+900;expired=False;last=-1
        while time.monotonic()<deadline:
            state=await client.state()
            status=summary(state)
            if status["attestations"]!=last:
                last=status["attestations"];print(f"height={state['height']} complete={status['complete']}/26 attestations={last} expired={status['expired_leases']}",flush=True)
            if not expired and state["height"]>lease["expires"]:
                spec=state["jobs"][lease["job"]]["spec"]
                stale=sign({"schema":"dawl.cluster-tx/1","chain_id":state["chain_id"],"plan_sha256":state["plan_sha256"],
                     "sequence":2,"valid_until":state["height"]+120,"kind":"result","payload":{"job":lease["job"],
                     "generation":lease["generation"],"report":{"outcome":"pass","checked_chunks":len(spec["positions"]),
                     "tokens_checked":spec["tokens"],"dense_bytes":spec["dense_bytes"],"implementation_sha256":state["plan"]["implementation_sha256"],
                     "execution_ms":1,"error":""}}},worker_key)
                rejected=await client.rpc("broadcast_tx_sync",{"tx":base64.b64encode(canonical_json(stale)).decode()})
                assert rejected["code"]!=0
                record("05-stale-lease-result-rejected",{"transaction":stale,"response":rejected,"height":state["height"]})
                await asyncio.to_thread(subprocess.run,["wsl","-d","kali-linux","-u","root","--exec","sh",
                    str(ROOT/"scripts/cluster_containers.sh").replace("C:\\","/mnt/c/").replace("\\","/"),"worker","0"],
                    check=True,capture_output=True,timeout=40)
                expired=True
            if expired and status["complete"]==status["jobs"]:
                record("06-entire-corpus-complete",status);break
            assert process.poll() is None,"host worker exited unexpectedly"
            await asyncio.sleep(2)
        else: raise TimeoutError("corpus work did not complete")
        release=await client.command("publish_dataset",{})
        training=await client.command("plan_training",{"dataset_release":release["dataset_release"],
                 "parent_weight":client.plan["imported_weight"]["id"],"recipe":{"executor":"causal-lm-next-token/v1",
                 "sequence_length":128,"seed":20261013,"requires":"TRAINER_GPU","optimizer":"adamw","learning_rate":"0.0003"}})
        record("07-dataset-and-training-plan",{"release":release,"training":training})
        await control("restart","--time","20","dawl-cluster-validator-3")
        await wait_for(lambda:client.state("http://127.0.0.1:8923"),lambda s:s["dataset_release"] is not None)
        states=[await client.state(f"http://127.0.0.1:{8920+i}") for i in range(4)]
        common=min(s["height"] for s in states)
        headers=[await client.rpc("block",{"height":str(common)},f"http://127.0.0.1:{8920+i}") for i in range(4)]
        assert len({h["block_id"]["hash"] for h in headers})==1
        assert len({h["block"]["header"]["app_hash"] for h in headers})==1
        assert all(s["dataset_release"]["id"]==release["dataset_release"] for s in states)
        record("08-restart-and-common-certified-history",{"common_height":common,"block_id":headers[0]["block_id"],
               "app_hash":headers[0]["block"]["header"]["app_hash"],"nodes":[summary(s) for s in states]})
        report.update(completed=True,seconds=round(time.perf_counter()-started,3),chain_id=state["chain_id"],
                      scope="Real CometBFT quorum loss and recovery, admitted full-corpus replay, one physical host; no training.")
        write_json(OUT/"report.json",report)
        print(json.dumps(report),flush=True)
    finally:
        # Restore validators on failure; preserve all data and any running workers.
        for index in (0,1):
            try: await control("start",f"dawl-cluster-validator-{index}")
            except Exception: pass
        await client.close()

if __name__=="__main__": asyncio.run(main())
