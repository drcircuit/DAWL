import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import torch
from dawl_lab.model import Config,LanguageModel
from dawl_lab.run import configure

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--cpu',action='store_true');a=p.parse_args()
device='cpu' if a.cpu else 'cuda';runtime=configure(device,'fp32','math',4)
torch.manual_seed(3);model=LanguageModel(Config(vocab_size=260,context=16,width=32,heads=4,kv_heads=2,layers=2,hidden=64)).to(device)
ids=torch.randint(3,260,(2,16),device=device);first=model(ids)[0];second=model(ids)[0]
if not torch.equal(first,second):raise SystemExit('Repeated forward pass differs')
loss=model(ids,ids)[1];loss.backward()
try:driver=subprocess.check_output(['/usr/lib/wsl/lib/nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],text=True).strip()
except (OSError,subprocess.SubprocessError):driver=None
value={'schema':'hypertrix.lab-doctor/1','utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,'python':platform.python_version(),
       'kernel':platform.release(),'driver':driver,'logical_cpus':os.cpu_count(),'storage_free_bytes':shutil.disk_usage(a.output.parent).free,
       'gpu_memory_bytes':torch.cuda.get_device_properties(0).total_memory if device=='cuda' else None,
       'forward_repeat_exact':True,'backpropagation_finite':all(torch.isfinite(p.grad).all().item() for p in model.parameters() if p.grad is not None),
       'scope':'Hardware and local forward/backward smoke test. Cross-GPU training replay requires separate measurement.'}
if not value['backpropagation_finite']:raise SystemExit('Nonfinite backward pass')
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(value,indent=2));print(json.dumps(value,indent=2))
