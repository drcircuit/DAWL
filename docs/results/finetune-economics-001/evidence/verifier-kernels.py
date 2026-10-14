"""Bounded paired prefix test of the pilot's default GPU optimization recipe."""
import gc,hashlib,math,time,json
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
import torch
import numpy as np
from peft import get_peft_model_state_dict
from dawl_finetune.run import CONFIG,seed,base_model,add_adapter
from dawl_finetune.data import load,file_hash
from dawl_node.common import read_json,write_json
root=Path('/pilot');start=time.time();deadline=read_json(root/'gpu-session.json')['deadline_unix']-30
if deadline-start<180:raise RuntimeError('Insufficient remaining GPU budget for paired prefix test')
data=load(root);values=np.concatenate([data['train_economics'],data['train_literature']])
order=np.random.default_rng(CONFIG['seed']+1).permutation(len(values))
expected_init=read_json(root/'replay-init-full.json')['adapter_hashes'];runs=[];saved=[]
for attempt in range(2):
    seed();model=base_model(root);torch.manual_seed(61014);model=add_adapter(model);model.config.use_cache=False;model.train()
    initial={k:hashlib.sha256(t.detach().cpu().numpy().tobytes()).hexdigest() for k,t in get_peft_model_state_dict(model).items()}
    parameters=[p for p in model.parameters() if p.requires_grad]
    opt=torch.optim.AdamW(parameters,lr=CONFIG['learning_rate'],weight_decay=CONFIG['weight_decay']);losses=[]
    total=math.ceil(len(values)/(CONFIG['batch_size']*CONFIG['accumulation']))*CONFIG['epochs']
    for step in range(10):
        if time.time()>deadline:raise RuntimeError('GPU deadline reached')
        indices=order[step*16:(step+1)*16];opt.zero_grad(set_to_none=True);group_loss=0.
        scale=min(1.,(step+1)/max(1,total*CONFIG['warmup_fraction']))
        for g in opt.param_groups:g['lr']=CONFIG['learning_rate']*scale
        for offset in range(0,len(indices),4):
            subset=indices[offset:offset+4];ids=torch.tensor(values[subset],device='cuda')
            loss=model(input_ids=ids,labels=ids,use_cache=False).loss
            (loss*len(subset)/len(indices)).backward();group_loss+=loss.detach().float().item()*len(subset)/len(indices)
        torch.nn.utils.clip_grad_norm_(parameters,CONFIG['clip_grad_norm'],error_if_nonfinite=True);opt.step();losses.append(group_loss)
    tensors={k:t.detach().cpu().clone() for k,t in get_peft_model_state_dict(model).items()};saved.append(tensors)
    runs.append({'attempt':attempt+1,'initial_hashes':initial,'initial_matches_recorded_recipe':initial==expected_init,'losses':losses})
    print({'attempt':attempt+1,'initial_matches_recipe':initial==expected_init,'first_loss':losses[0],'last_loss':losses[-1]},flush=True)
    del model,opt,parameters;gc.collect();torch.cuda.empty_cache()
equal=sum(torch.equal(saved[0][k],saved[1][k]) for k in saved[0])
result={'schema':'hypertrix.gpu-prefix-repeatability/1','steps_per_attempt':10,'runs':runs,
        'initial_tensors_equal':runs[0]['initial_hashes']==runs[1]['initial_hashes'],
        'final_exact_tensors':equal,'final_tensor_count':len(saved[0]),
        'final_max_abs_difference':max(float((saved[0][k]-saved[1][k]).abs().max()) for k in saved[0]),
        'deterministic_algorithms_enabled':torch.are_deterministic_algorithms_enabled(),
        'cudnn_deterministic':torch.backends.cudnn.deterministic,'allow_tf32':torch.backends.cuda.matmul.allow_tf32,
        'wall_seconds':time.time()-start,'script_sha256':file_hash(Path(__file__)),
        'scope':'Two ten-update prefixes in one process with reset seed, identical initial tensors, recipe and data order. This is a diagnostic, not a full checkpoint verification.'}
write_json(root/'replay-kernel-diagnostic.json',result);print({k:v for k,v in result.items() if k!='runs'},flush=True)
