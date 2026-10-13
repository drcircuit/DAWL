"""Archive an allowlist of public cluster evidence; never include keys or live databases."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root",type=Path,required=True)
parser.add_argument("--run",type=Path,required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
def copy(source,name=None):
    data=source.read_bytes()
    if b"PRIVATE KEY" in data or b'"priv_key"' in data: raise ValueError("private key material is not public evidence")
    (args.output/(name or source.name)).write_bytes(data)
for path in sorted(args.run.glob("*.json")):
    if path.name!="host-worker-process.json": copy(path)
for name in ("deployment.json","plan.json","genesis.json","training-input-check.json"):
    copy(args.root/name)
copy(ROOT/"artifacts/cluster-tests-development.log","tests.log")
certificate=json.loads((args.run/"public-certified-state.json").read_text())
state=certificate["state"]
counts=Counter()
for job in state["jobs"].values():
    for public,attestation in job["attestations"].items():
        counts[state["plan"]["actors"][public]["name"]]+=1
code="import sqlite3,json; c=sqlite3.connect('/node/worker.sqlite'); print(json.dumps(dict(c.execute('SELECT name,value FROM counters'))))"
metrics={}
for i in (0,1,2):
    result=subprocess.run(["wsl","-d","kali-linux","-u","root","--exec","podman","exec",f"dawl-cluster-worker-{i}",
                           "python","-c",code],capture_output=True,text=True,check=True)
    metrics[f"worker-{i}"]=json.loads(result.stdout)
with closing(sqlite3.connect(args.root/"worker-3/worker.sqlite")) as db:
    metrics["worker-3"]=dict(db.execute("SELECT name,value FROM counters"))
runtime=json.loads(subprocess.run(["wsl","-d","kali-linux","-u","root","--exec","podman","inspect",
                                   "dawl-cluster-validator-2"],capture_output=True,text=True,check=True).stdout)[0]
report={"schema":"dawl.cluster-capture/1","image_id":runtime["Image"],
        "code_commit":subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip(),
        "implementation_sha256":state["plan"]["implementation_sha256"],"cometbft":"0.38.26","validator_count":4,
        "final_certificate_height":certificate["certificate_height"],"final_app_hash":certificate["app_hash"],
        "attestations_by_worker":dict(counts),"transfer_counters":metrics,"training_executed":False,
        "baseline_checkpoint_unchanged":state["plan"]["lineage_checkpoint"],
        "limits":["One physical host/operator","Static validator and worker admission","Worker signatures are computation claims, not proofs",
                  "Full-state hashing is bounded, not a sharded state database","No model training, weight promotion or token rewards"]}
(args.output/"capture.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(args.output.iterdir())}
(args.output/"artifact-index.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report,indent=2))
