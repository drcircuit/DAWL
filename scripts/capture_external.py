"""Archive public rehearsal observations, the public bootstrap bundle and test output."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run",type=Path,default=Path("artifacts/external-run-001"))
parser.add_argument("--output",type=Path,default=Path("docs/results/external-run-001"))
parser.add_argument("--source-commit",required=True,help="commit used for the full-corpus rehearsal")
args=parser.parse_args()
report=json.loads((args.run/"report.json").read_text())
if report.get("completed") is not True or report.get("lab_containers_stopped") is not True:
    raise ValueError("rehearsal has not completed")
args.output.mkdir(parents=True,exist_ok=False)
paths=list(args.run.glob("*.json"))+list((args.run/"network").glob("*.json"))
for path in paths:
    data=path.read_bytes()
    if path.is_symlink() or b'PRIVATE KEY' in data or b'"priv_key"' in data:
        raise ValueError("private material or symbolic link in public evidence")
    target=args.output/path.relative_to(args.run)
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
tests=Path("artifacts/external-tests-final.log").read_bytes()
if not tests.rstrip().endswith(b'OK'):
    raise ValueError("final test suite did not pass")
(args.output/"tests.log").write_bytes(tests)
(args.output/"postrun-tests.log").write_bytes(Path("artifacts/external-postrun-tests.log").read_bytes())
capture={"schema":"dawl.external-capture/1",
         "source_commit":subprocess.check_output(["git","rev-parse",args.source_commit+"^{commit}"],text=True).strip(),
         "post_run_change":"Enable container init; separately tested shutdown and 20 focused tests after the full-corpus run",
         "private_keys_included":False,"external_machines_used":False,
         "compose_engine_validation":"Unavailable on this host; rehearsal uses direct Podman commands",
         "limits":["One physical host and administrator","Private mesh configuration is an operator prerequisite",
                   "No economic penalties, dynamic validator membership, or training",
                   "No live malicious-validator double-vote injection"]}
(args.output/"capture.json").write_text(json.dumps(capture,indent=2)+"\n",encoding="utf-8")
index={p.relative_to(args.output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
       for p in sorted(args.output.rglob("*")) if p.is_file()}
(args.output/"artifact-index.json").write_text(json.dumps(index,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"output":str(args.output),"files":len(index)}))
