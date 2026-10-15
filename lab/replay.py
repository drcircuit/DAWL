"""Replay a public checkpoint package using a separate verifier identity.

Only training implementations already bundled in the trusted release are run.
Downloaded Python files are evidence, not an executable plugin mechanism.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True)
p.add_argument('--root',type=Path,required=True);p.add_argument('--start',type=int,required=True);p.add_argument('--end',type=int,required=True)
p.add_argument('--expected-operator',required=True);p.add_argument('--device',choices=['cpu','cuda'],default='cuda');p.add_argument('--execute',action='store_true')
a=p.parse_args();kit=Path(__file__).resolve().parents[1]
index=json.loads((kit/'lab/runtime-index.json').read_bytes())
identity=hashlib.sha256((a.run/'implementation.json').read_bytes()).hexdigest()
if identity not in index:raise SystemExit('This training implementation is not approved in this release. Use its matching reviewed kit.')
source=(kit/index[identity]).resolve()
if not source.is_relative_to(kit):raise SystemExit('Unsafe runtime index')
if not a.execute:
    env={**os.environ,'PYTHONPATH':str(source),'PYTHONSAFEPATH':'1'}
    raise SystemExit(subprocess.run([sys.executable,'-P',str(Path(__file__).resolve()),*sys.argv[1:],'--execute'],env=env).returncode)

import numpy as np
import torch
from dawl_lab.run import configure,check_implementation,validate_lineage,load_checkpoint,update,Resources
from dawl_lab.data import load
from dawl_node.common import keygen,load_key,sign,write_json
from dawl_scratch.data import file_hash
from dawl_seed.progress import progress

validate_lineage(a.run,a.expected_operator);check_implementation(a.run)
recipe=json.loads((a.run/'recipe.json').read_bytes())
if not 0<=a.start<a.end<=recipe['steps']:raise ValueError('Invalid replay interval')
runtime=configure(a.device,recipe['runtime']['precision'],recipe['runtime']['attention'],recipe['runtime']['threads'])
if runtime!=recipe['runtime']:raise ValueError('Exact replay requires the recorded hardware/runtime cohort')
bp,arrays=load(a.dataset)
if file_hash(a.dataset/'blueprint.json')!=recipe['blueprint_sha256']:raise ValueError('Blueprint differs')
model,opt,start=load_checkpoint(a.run,a.start,a.device,True);expected,expected_opt,end=load_checkpoint(a.run,a.end,'cpu',True)
work=np.load(a.run/'schedule.npy',mmap_mode='r')
compute=torch.compile(model) if recipe.get('compiler')=='torch-inductor-default' else model
a.root.mkdir(parents=True,exist_ok=False);public=keygen(a.root/'verifier.private.pem',a.root/'verifier.public.key')
began=time.time();utc=datetime.now(timezone.utc).isoformat();meter=Resources(a.root,a.device);meter.start()
try:
    for step in range(a.start,a.end):
        update(compute,opt,arrays['train'],work[step],recipe,step,a.device)
        if (step-a.start)%10==0:progress(a.root,'Replaying public checkpoint',job='Independent replay',role='model-replay',status='running',completed=step-a.start+1,total=a.end-a.start,unit='steps')
finally:resources=meter.finish()
checks=[{'name':k,'exact':torch.equal(v.cpu(),expected.state_dict()[k]),'max_absolute':float((v.cpu()-expected.state_dict()[k]).abs().max())} for k,v in model.state_dict().items()]
optimizer_exact=True
for (_,left),(_,right) in zip(model.named_parameters(),expected.named_parameters()):
    la=opt.state.get(left,{});ra=expected_opt.state.get(right,{})
    if la.keys()!=ra.keys() or any(not torch.equal(v.cpu(),ra[k]) for k,v in la.items()):optimizer_exact=False
accepted=all(row['exact'] for row in checks) and optimizer_exact
body={'schema':'hypertrix.lab-independent-replay/1','accepted':accepted,'verifier':public,'model_operator':a.expected_operator,
      'start_step':a.start,'end_step':a.end,'start_state_sha256':start['state_sha256'],'end_state_sha256':end['state_sha256'],
      'recipe_sha256':file_hash(a.run/'recipe.json'),'blueprint_sha256':recipe['blueprint_sha256'],'implementation_sha256':identity,
      'runtime':runtime,'tensor_count':len(checks),'exact_tensors':sum(row['exact'] for row in checks),'optimizer_exact':optimizer_exact,
      'checks':checks,'resources':resources,'started_at':utc,'finished_at':datetime.now(timezone.utc).isoformat(),
      'calendar_elapsed_seconds':time.time()-began,'active_elapsed_seconds':resources['wall_seconds'],
      'scope':'Separate verifier identity; input artifacts read only; same recorded runtime; stated interval only. Calendar elapsed includes suspension, active elapsed uses the runtime monotonic clock.'}
write_json(a.root/'receipt.json',sign(body,load_key(a.root/'verifier.private.pem')))
progress(a.root,'Public replay verified' if accepted else 'Public replay mismatch',job='Independent replay',role='model-replay',status='complete' if accepted else 'failed',completed=a.end-a.start,total=a.end-a.start,unit='steps')
print(json.dumps({k:body[k] for k in ('accepted','verifier','model_operator','tensor_count','exact_tensors','optimizer_exact','start_step','end_step')}))
if not accepted:raise SystemExit(1)
