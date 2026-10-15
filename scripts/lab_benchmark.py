"""Measure actual GPU throughput/memory before fixing a training recipe."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
import numpy as np
import torch
from dawl_lab.model import Config,LanguageModel
from dawl_lab.run import configure,optimizer_for,update
from dawl_node.common import write_json

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--compile',action='store_true');p.add_argument('--batch',type=int);a=p.parse_args();a.root.mkdir(parents=True,exist_ok=True)
runtime=configure('cuda');rows=[]
for kv in ((4,) if a.compile else (4,12)):
    for batch in ((a.batch,) if a.batch else (16,) if a.compile else (4,8,16)):
        torch.manual_seed(20261015);config=Config(kv_heads=kv);model=LanguageModel(config).cuda()
        recipe={'learning_rate':.0006,'pad_id':2,'steps':30,'micro_batch':batch,'runtime':runtime}
        opt=optimizer_for(model,recipe);values=np.random.default_rng(1).integers(3,config.vocab_size,(batch,config.context+1))
        if a.compile:model=torch.compile(model)
        torch.cuda.reset_peak_memory_stats();start=time.monotonic()
        try:
            for step in range(3):update(model,opt,values,np.arange(batch),recipe,step,'cuda')
            torch.cuda.synchronize();start=time.monotonic()
            for step in range(3,9):update(model,opt,values,np.arange(batch),recipe,step,'cuda')
            torch.cuda.synchronize();elapsed=time.monotonic()-start
            row={'config':asdict(config),'batch':batch,'parameters':sum(p.numel() for p in model.parameters()),
                 'tokens_per_second':6*batch*config.context/elapsed,'timed_seconds':elapsed,
                 'peak_allocated_bytes':torch.cuda.max_memory_allocated(),'status':'ok'}
        except torch.OutOfMemoryError:
            row={'config':asdict(config),'batch':batch,'status':'out-of-memory'}
        rows.append(row);print(json.dumps(row),flush=True)
        del model,opt;torch.cuda.empty_cache()
write_json(a.root/'benchmark.json',{'runtime':runtime,'compiled':a.compile,'results':rows,'scope':'Synthetic random tokens; includes optimizer; no language-quality claim.'})
