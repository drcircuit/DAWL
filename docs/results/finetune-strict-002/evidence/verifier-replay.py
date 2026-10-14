"""Fresh-process replay of every published economics checkpoint.

Same host/runtime, same public recipe, no optimizer state or adapter weights
loaded from the trainer. Exact tensor equality is the pass condition. Measured
differences are reported on failure, never silently accepted by a tolerance.
"""
import argparse
from datetime import datetime,timezone
import math
import json
from pathlib import Path
import sys
import time
import numpy as np
import torch
from peft import get_peft_model_state_dict
from safetensors.torch import load_file, save_file
sys.path.insert(0,'/app/src')
from dawl_finetune.run import CONFIG,seed,base_model,tokenizer,chat_ids,add_adapter,activate_recipe,optimization_options,clipping_options
from dawl_finetune.execution import replay_outcome
from dawl_finetune.data import load,file_hash
from dawl_node.common import read_json,write_json

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/pilot'));a=p.parse_args();root=a.root
if (root/'replay-verification.json').exists():raise ValueError('Replay result already exists')
start=time.time();deadline=read_json(root/'gpu-session.json')['deadline_unix']-30
summary=read_json(root/'training-summary.json');recipe=read_json(root/'recipe.json')
activate_recipe(recipe)
seed();data=load(root);tok=tokenizer(root);model=base_model(root)
# Each baseline generation resets the Torch RNG. Only the last generation can
# affect adapter initialization: that final manual_seed erases prior RNG history.
# Replay that exact call, including sampling, instead of spending the GPU budget
# recomputing earlier probes that are followed by another explicit RNG reset.
with torch.inference_mode():
    model.eval();torch.manual_seed(61014)
    ids=chat_ids(tok,'What should the Bank of England do during a banking panic according to Bagehot?')
    model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=220,
                   do_sample=True,temperature=.7,top_p=.8,top_k=20,pad_token_id=tok.eos_token_id)
model=add_adapter(model);model.config.use_cache=False
parameters=[p for p in model.parameters() if p.requires_grad]
optimizer=torch.optim.AdamW(parameters,lr=CONFIG['learning_rate'],weight_decay=CONFIG['weight_decay'],**optimization_options())
values=np.concatenate([data['train_economics'],data['train_literature']])
batch=CONFIG['batch_size'];accum=CONFIG['accumulation'];total=math.ceil(len(values)/(batch*accum))*CONFIG['epochs']
step=0;checks=[];budget_stop=False
for checkpoint in summary['epochs']:
    epoch=checkpoint['epoch'];order=np.random.default_rng(CONFIG['seed']+epoch).permutation(len(values));model.train()
    for group in range(0,checkpoint['epoch_windows'],batch*accum):
        if time.time()>deadline:budget_stop=True;break
        indices=order[group:min(group+batch*accum,checkpoint['epoch_windows'])];optimizer.zero_grad(set_to_none=True)
        progress=step/max(1,total);warmup=CONFIG['warmup_fraction']
        scale=min(1.,(step+1)/max(1,total*warmup)) if progress<warmup else .1+.9*.5*(1+math.cos(math.pi*(progress-warmup)/(1-warmup)))
        for g in optimizer.param_groups:g['lr']=CONFIG['learning_rate']*scale
        for offset in range(0,len(indices),batch):
            subset=indices[offset:offset+batch];ids=torch.tensor(values[subset],device='cuda')
            loss=model(input_ids=ids,labels=ids,use_cache=False).loss
            if not torch.isfinite(loss):raise ValueError('Nonfinite replay loss')
            (loss*len(subset)/len(indices)).backward()
        torch.nn.utils.clip_grad_norm_(parameters,CONFIG['clip_grad_norm'],error_if_nonfinite=True,**clipping_options());optimizer.step();step+=1
        if step%20==0:
            print(f'Replay epoch {epoch}, update {step}, elapsed {time.time()-start:.0f}s',flush=True)
            progress={'epoch':epoch,'step':step,'total_steps':summary['epochs'][-1]['optimizer_steps'],
                      'elapsed_seconds':time.time()-start,'utc':datetime.now(timezone.utc).isoformat()}
            pending=root/'replay-progress.pending';pending.write_text(json.dumps(progress));pending.replace(root/'replay-progress.json')
    if budget_stop:break
    expected=load_file(str(root/'checkpoints'/checkpoint['name']/'adapter_model.safetensors'),device='cpu')
    actual={name:t.detach().cpu() for name,t in get_peft_model_state_dict(model).items()}
    if expected.keys()!=actual.keys():raise ValueError('Adapter tensor schema differs')
    # Retain the actual comparison candidate, including failures, for later
    # effective-update and behavioral analysis without repeating GPU work.
    candidate=root/'replay-checkpoints'/checkpoint['name'];candidate.mkdir(parents=True,exist_ok=False)
    candidate_file=candidate/'adapter_model.safetensors';save_file(actual,str(candidate_file))
    equal=0;max_abs=0.;squared_error=0.;squared_reference=0.
    for name,target in expected.items():
        result=actual[name]
        if torch.equal(result,target):equal+=1
        difference=(result.float()-target.float()).double();max_abs=max(max_abs,float(difference.abs().max()))
        squared_error+=float(difference.square().sum());squared_reference+=float(target.double().square().sum())
    check={'checkpoint':checkpoint['name'],'optimizer_steps':step,'tensor_count':len(expected),'exact_tensors':equal,
           'exact_match':equal==len(expected),'max_abs_difference':max_abs,
           'relative_l2':math.sqrt(squared_error/max(squared_reference,1e-30)),
           'expected_checkpoint_sha256':file_hash(root/'checkpoints'/checkpoint['name']/'adapter_model.safetensors'),
           'replayed_checkpoint_sha256':file_hash(candidate_file),'replayed_checkpoint_file':candidate_file.relative_to(root).as_posix()}
    checks.append(check);print(check,flush=True)
    # Preserve each completed boundary even if the outer runtime timeout fires
    # before the final receipt. This never upgrades a partial replay to a pass.
    write_json(root/f'replay-checkpoint-{epoch}.json',check)
status=replay_outcome(checks,len(summary['epochs']),budget_stop)
result={'schema':'hypertrix.local-full-training-replay/1','status':status,'checks':checks,'completed_utc':datetime.now(timezone.utc).isoformat(),
        'wall_seconds':time.time()-start,'recipe_sha256':file_hash(root/'recipe.json'),'dataset_lock_sha256':file_hash(root/'dataset-lock.json'),
        'verifier_script_sha256':file_hash(Path(__file__)),'gpu':torch.cuda.get_device_name(0),
        'scope':'Full local optimization replay from the frozen base in a fresh container/process, comparing every published economics adapter tensor. Same GPU, runtime and operator; not an independent organization, cross-hardware guarantee, Oracle certificate, or proof of correct financial advice.'}
write_json(root/'replay-verification.json',result);print(result,flush=True)
