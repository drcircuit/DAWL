"""Linux/WSL isolated bootstrap rehearsal; no external machines or live-chain mutation.

Requires a freshly built local image, rootful Podman and the preserved v2 provider.
Retains all state, restores disconnected validators, and stops only this lab at exit.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import Request, urlopen

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--native",type=Path,default=Path("/var/lib/dawl/external-lab-001"))
parser.add_argument("--output",type=Path,default=Path("artifacts/external-run-001"))
parser.add_argument("--image",default="localhost/dawl-cluster:external-v1")
args=parser.parse_args()
ROOT=args.native.resolve();OUT=args.output.resolve();NETWORK="dawl-external-lab"
if not str(ROOT).startswith("/var/lib/dawl/external-lab-") or ROOT.exists() or OUT.exists():
    raise SystemExit("fresh, dedicated lab directories required")
ROOT.mkdir(parents=True);OUT.mkdir(parents=True)
CREATED=[];DISCONNECTED=[]
MEMBERS=[(f"validator-{i}","validator",f"10.89.91.{10+i}") for i in range(4)]
MEMBERS += [(f"provider-{i}","provider",f"10.89.91.{20+i}") for i in range(2)]
MEMBERS += [(f"worker-{i}","worker",f"10.89.91.{30+i}") for i in range(4)]
MEMBERS += [("publisher","publisher","10.89.91.40")]
START=time.monotonic();REPORT={"scope":"Isolated single-host rehearsal; simulated operators, not independent machines", "phases":[]}


def command(*parts,timeout=120,check=True):
    return subprocess.run(list(parts),capture_output=True,text=True,check=check,timeout=timeout)


def podman(*parts,**kw): return command("podman",*parts,**kw)


def save(name,data):
    (OUT/(name+".json")).write_text(json.dumps(data,indent=2)+"\n")
    REPORT["phases"].append({"name":name,"seconds":round(time.monotonic()-START,3)})
    print(name,flush=True)


def local(name,*parts,extra=(),network="none"):
    parent=ROOT/"operators"/name
    return podman("run","--rm","--network",network,"-v",f"{parent}:/operator",*extra,
                  "--entrypoint","python",args.image,*parts).stdout


def start(name,role,host):
    cmd=["validator","/node"] if role=="validator" else (["provider","--root","/node","--host",host,
            "--port","8900","--allow-insecure-lab"] if role=="provider" else ["worker","--root","/node"])
    ports=["-p",f"127.0.0.1:{8950+int(name[-1])}:26657"] if role=="validator" else []
    options=[]
    if role=="publisher": options=["--entrypoint","sleep"];cmd=["infinity"]
    container="dawl-external-"+name
    podman("run","-d","--name",container,"--label","org.dawl.external=lab-001","--network",NETWORK,"--ip",host,
           *ports,"--read-only","--tmpfs","/tmp:size=256m","--cap-drop","ALL","--security-opt","no-new-privileges",
           "--cpus","2","--memory","2g","-v",f"{ROOT/'operators'/name/'node'}:/node",*options,args.image,*cmd)
    CREATED.append(container)


def rpc(method,params=None,index=2):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params or {}}).encode()
    with urlopen(Request(f"http://127.0.0.1:{8950+index}",data=body,headers={"Content-Type":"application/json"}),timeout=5) as response:
        result=json.load(response)
    if "error" in result: raise ValueError(result["error"])
    return result["result"]


def state():
    return json.loads(base64.b64decode(rpc("abci_query",{"path":"/state","data":"","prove":False})["response"]["value"]))


def wait(predicate,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            value=predicate()
            if value: return value
        except (OSError,ValueError,KeyError): pass
        time.sleep(1)
    raise TimeoutError("lab condition did not arrive")


def actor(name,kind,payload):
    code="""import asyncio,json,sys;sys.path.insert(0,'/app/src')
from pathlib import Path
from dawl_cluster.client import Client
from dawl_node.common import load_key
async def main():
 c=Client(Path('/operator/node'),load_key(Path('/operator/node/key.private.pem')))
 try: print(json.dumps(await c.command(sys.argv[1],json.loads(sys.argv[2]))))
 finally: await c.close()
asyncio.run(main())"""
    return json.loads(local(name,"-c",code,kind,json.dumps(payload),network=NETWORK))


def main():
    image=json.loads(podman("image","inspect",args.image).stdout)[0]
    digest=image["Digest"]
    assert digest.startswith("sha256:")
    pin_image=args.image.split(":")[0]+"@"+digest
    ceremony=ROOT/"ceremony";ceremony.mkdir();shutil.chown(ceremony,user=1000,group=1000)
    registrations=ceremony/"registrations";registrations.mkdir();shutil.chown(registrations,user=1000,group=1000)
    for name,role,host in MEMBERS:
        parent=ROOT/"operators"/name;parent.mkdir(parents=True);shutil.chown(parent,user=1000,group=1000)
        local(name,"/app/dawl_external.py","identity","--root","/operator/node","--name",name,"--role",role,"--host",host)
        shutil.copyfile(parent/"node/registration.json",registrations/(name+".json"))
    # The assembler sees public registration exports and the keyless provider only.
    assembled=json.loads(podman("run","--rm","--network","none","-v",f"{ceremony}:/ceremony",
        "-v","/var/lib/dawl/cluster-v2/provider-0:/template:ro","--entrypoint","python",args.image,
        "/app/dawl_external.py","assemble","--root","/ceremony/network","--template","/template",
        "--chain-id","dawl-external-lab-001","--image",pin_image,"--registrations",
        *["/ceremony/registrations/"+name+".json" for name,_,_ in MEMBERS]).stdout)
    bundle=ceremony/"network"
    for name,_,_ in MEMBERS:
        local(name,"/app/dawl_external.py","install","--root","/operator/node","--bundle","/bundle",
              "--network-sha256",assembled["network_sha256"],extra=("-v",f"{bundle}:/bundle:ro"))
    for name in ("provider-0","provider-1"):
        local(name,"/app/dawl_external.py","seed-provider","--root","/operator/node","--objects","/source",
              extra=("-v","/var/lib/dawl/cluster-v2/provider-0/objects:/source:ro"))
    assert len({(ROOT/"operators"/n/"node/comet/config/genesis.json").read_bytes() for n,r,h in MEMBERS if r=="validator"})==1
    for p in bundle.iterdir():
        assert b'"priv_key"' not in p.read_bytes() and b'PRIVATE KEY' not in p.read_bytes()
    save("01-public-bootstrap",{**assembled,"image_id":image["Id"],"image_manifest_digest":digest,
                                "operator_key_isolation":"Only public registration files were mounted into assembly", "nodes":len(MEMBERS)})
    shutil.copytree(bundle,OUT/"network")
    podman("network","create","--disable-dns","--subnet","10.89.91.0/24",NETWORK)
    for member in MEMBERS:
        if member[1] in ("validator","provider","publisher"): start(*member)
    wait(lambda:state()["height"]>=3)
    initial_height=state()["height"]
    name="dawl-external-validator-0";podman("network","disconnect",NETWORK,name);DISCONNECTED.append(name)
    wait(lambda:state()["height"]>=initial_height+4)
    save("02-one-isolated-validator",{"height_before":initial_height,"height_after":state()["height"],"network_isolation":True})
    name="dawl-external-validator-1";podman("network","disconnect",NETWORK,name);DISCONNECTED.append(name)
    time.sleep(4);halted=state()["height"];time.sleep(8)
    assert state()["height"]==halted
    save("03-quorum-loss",{"height":halted,"halt_seconds":8,"connected_validators":2})
    for name in list(DISCONNECTED):
        index=int(name[-1]);podman("network","connect","--ip",f"10.89.91.{10+index}",NETWORK,name);DISCONNECTED.remove(name)
    wait(lambda:state()["height"]>=halted+3)
    lease=actor("worker-0","claim",{"capability":"AUDIT_CPU"})
    save("04-recovered-and-abandoned-lease",lease)
    # Fault proxies are only reachable inside this isolated lab bridge.
    for name,address,port,backend,mode in (("rpc-liar","10.89.91.60",26657,"http://10.89.91.12:26657","tamper-state"),
                                          ("provider-liar","10.89.91.61",8900,"http://10.89.91.21:8900","corrupt-piece")):
        container="dawl-external-"+name
        podman("run","-d","--name",container,"--network",NETWORK,"--ip",address,"--entrypoint","python",args.image,
               "/app/scripts/external_fault_proxy.py","--backend",backend,"--port",str(port),"--mode",mode)
        CREATED.append(container)
    verifier=["run","--rm","--network",NETWORK,"-v",f"{bundle/'genesis.json'}:/genesis.json:ro",
              "--entrypoint","dawl-consensus",args.image,"verify","--genesis","/genesis.json",
              "--genesis-sha256",assembled["genesis_sha256"]]
    bad=podman(*verifier,"--rpc","http://10.89.91.60:26657",check=False)
    assert bad.returncode!=0 and "state bytes differ" in bad.stderr,bad.stderr
    save("05-forged-rpc-rejected",{"exit_code":bad.returncode,"reason":bad.stderr.strip()})
    podman("stop","--time","10","dawl-external-provider-0")
    for name,role,host in MEMBERS:
        if role=="worker":
            path=ROOT/"operators"/name/"node/cluster.json";config=json.loads(path.read_text())
            config["rpcs"].insert(0,"http://10.89.91.60:26657")
            config["providers"].insert(0,"http://10.89.91.61:8900")
            path.write_text(json.dumps(config))
            if name!="worker-0": start(name,role,host)
    deadline=time.monotonic()+1500;started_zero=False;last=-1
    while time.monotonic()<deadline:
        current=state()
        count=sum(len(j["attestations"]) for j in current["jobs"].values())
        if count!=last: print(f"height={current['height']} attestations={count}/52",flush=True);last=count
        if not started_zero and current["height"]>lease["expires"]:
            assert current["incidents"]["counts"]["lease_expired"]>=1
            save("06-timeout-attributed",current["incidents"])
            start("worker-0","worker","10.89.91.30");started_zero=True
        if count==52: break
        time.sleep(3)
    else: raise TimeoutError("corpus did not complete under network faults")
    podman("start","dawl-external-provider-0")
    released=actor("publisher","publish_dataset",{})
    certified=json.loads(podman(*verifier,"--rpc","http://10.89.91.12:26657","--max-age","120s").stdout)
    save("07-full-corpus-certificate",certified)
    assert certified["state"]["dataset_release"]["id"]==released["dataset_release"]
    counts={}
    for i in range(4):
        code="import sqlite3,json;print(json.dumps(dict(sqlite3.connect('/node/worker.sqlite').execute('SELECT name,value FROM counters'))))"
        counts[f"worker-{i}"]=json.loads(podman("exec",f"dawl-external-worker-{i}","python","-c",code).stdout)
    assert sum(v.get("certificate_failures",0) for v in counts.values())>0
    assert all(v.get("certified_state_reads",0)>0 for v in counts.values())
    save("08-worker-verification-counters",counts)
    REPORT.update(completed=True,seconds=round(time.monotonic()-START,3),training_executed=False,
                  dataset_release=released["dataset_release"],chunks=1438,attestations=52,
                  fault_scope=["validator network disconnection","forged RPC state","one unavailable provider",
                               "corrupt artifact pieces with 100ms delay","abandoned worker lease"],
                  not_tested=["independent operators","WAN performance","validator double-vote attack","economic penalties"])


try:
    main()
finally:
    for name in DISCONNECTED:
        podman("network","connect","--ip",f"10.89.91.{10+int(name[-1])}",NETWORK,name,check=False)
    for name in reversed(CREATED):
        podman("stop","--time","20",name,check=False)
    REPORT["lab_containers_stopped"]=True
    (OUT/"report.json").write_text(json.dumps(REPORT,indent=2)+"\n")
