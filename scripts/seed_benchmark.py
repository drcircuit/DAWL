"""Measure useful target-token throughput and checkpoint-resume reproducibility."""
import argparse
import gc
import json
from pathlib import Path
import time
import torch
from dawl_node.common import keygen,write_json
from dawl_seed.model import Config,LanguageModel
from dawl_seed.data import load
from dawl_seed.run import configure,make_optimizer,update,save_checkpoint,load_checkpoint
from dawl_seed.progress import progress

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True);p.add_argument('--attention',choices=['math','flash'],default='math');a=p.parse_args()
a.root.mkdir(parents=True,exist_ok=True);bp,arrays=load(a.dataset);results=[]
settings=[('bf16',32),('bf16',64),('bf16',128)] if a.attention=='flash' else [('fp32',16),('bf16',16),('bf16',32),('bf16',64)]
for precision,batch in settings:
    runtime=configure('cuda',precision,a.attention);torch.manual_seed(20261015)
    model=LanguageModel(Config()).cuda();opt=None
    recipe={'learning_rate':.0006,'pad_id':bp['pad_id'],'max_steps':100,'router_balance_weight':.01,'runtime':runtime}
    root=a.root/(precision+'-'+str(batch));root.mkdir()
    write_json(root/'config.json',model.config.__dict__);write_json(root/'recipe.json',recipe)
    keygen(root/'operator.private.pem',root/'operator.public.key')
    opt=make_optimizer(model,recipe);torch.cuda.reset_peak_memory_stats()
    result={'precision':precision,'batch_size':batch,'attention':a.attention,'blueprint_sha256':__import__('dawl_seed.data',fromlist=['file_hash']).file_hash(a.dataset/'blueprint.json')}
    try:
        progress(a.root,'Benchmarking batches',job='Seed II GPU benchmark',role='model-trainer',status='running',completed=len(results),total=len(settings),unit='configurations',configuration=precision+'/'+str(batch)+'/'+a.attention)
        for step in range(3):update(model,opt,arrays['train'],list(range(step*batch,(step+1)*batch)),recipe,step,'cuda')
        torch.cuda.synchronize();began=time.monotonic();tokens=0
        for step in range(3,13):
            row=update(model,opt,arrays['train'],list(range(step*batch,(step+1)*batch)),recipe,step,'cuda');tokens+=row['tokens']
        torch.cuda.synchronize();elapsed=time.monotonic()-began
        result.update(targets_per_second=tokens/elapsed,elapsed_seconds=elapsed,peak_vram_mib=torch.cuda.max_memory_allocated()/1024**2)
        save_checkpoint(root,model,opt,13)
        update(model,opt,arrays['train'],list(range(13*batch,14*batch)),recipe,13,'cuda')
        expected={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
        expected_opt=[{k:v.detach().cpu().clone() for k,v in opt.state[x].items()} for x in model.parameters()]
        del model,opt;gc.collect();torch.cuda.empty_cache()
        model,opt,_=load_checkpoint(root,13,'cuda',True)
        update(model,opt,arrays['train'],list(range(13*batch,14*batch)),recipe,13,'cuda')
        result['resume_exact']=all(torch.equal(v.cpu(),expected[k]) for k,v in model.state_dict().items())
        result['optimizer_exact']=all(all(torch.equal(opt.state[x][k].cpu(),v) for k,v in expect.items()) for x,expect in zip(model.parameters(),expected_opt))
    except torch.cuda.OutOfMemoryError:
        result['status']='out-of-memory'
    results.append(result);print(json.dumps(result),flush=True)
    del model,opt;gc.collect();torch.cuda.empty_cache()
write_json(a.root/'results.json',results)
progress(a.root,'Benchmark complete',job='Seed II GPU benchmark',role='model-trainer',status='complete',completed=len(settings),total=len(settings),unit='configurations')
