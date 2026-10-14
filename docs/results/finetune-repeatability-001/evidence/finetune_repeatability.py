"""Fresh-process ablation of the frozen pilot. Source directory is read-only.

Run `matrix` in a bounded CUDA container; each attempt is a separate interpreter.
All acceptance is descriptive here: no numerical tolerance promotes checkpoints.
"""
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

sys.path.insert(0, '/app/src')
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dawl_node.common import read_json, write_json, canonical_json, sign, load_key
from dawl_finetune.data import file_hash
from execution import POLICIES, configure, optimizer_options


def tensor_hash(t):
    import torch
    t = t.detach().cpu().contiguous()
    return hashlib.sha256(t.reshape(-1).view(torch.uint8).numpy().tobytes()).hexdigest()


def hashes(tensors):
    return {k: tensor_hash(v) for k, v in sorted(tensors.items())}


def snapshot(model, gradients=False):
    from peft import get_peft_model_state_dict
    if gradients:
        return {k: p.grad.detach().cpu().clone() for k, p in model.named_parameters()
                if p.requires_grad and p.grad is not None}
    return {k: v.detach().cpu().clone() for k, v in get_peft_model_state_dict(model).items()}


def worker(a):
    import numpy as np
    import torch
    from safetensors.torch import save_file
    from dawl_finetune.run import CONFIG, seed, base_model, add_adapter
    from dawl_finetune.data import load
    start = time.time(); out = a.out; out.mkdir(exist_ok=False)
    seed(); settings = configure(a.policy)
    data = load(a.source)
    values = np.concatenate([data['train_economics'], data['train_literature']])
    order = np.random.default_rng(CONFIG['seed'] + 1).permutation(len(values))
    model = base_model(a.source)
    # The original initialization diagnostic established these exact tensors.
    torch.manual_seed(61014); model = add_adapter(model); model.config.use_cache = False; model.train()
    initial = snapshot(model)
    expected = read_json(a.source/'replay-init-full.json')['adapter_hashes']
    initial_hashes = hashes(initial)
    if initial_hashes != expected: raise ValueError('Initialization differs from frozen pilot')
    save_file(initial, str(out/'initial.safetensors'))
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=CONFIG['learning_rate'],
                                 weight_decay=CONFIG['weight_decay'], **optimizer_options(a.policy))
    batch, accum = CONFIG['batch_size'], CONFIG['accumulation']
    total = math.ceil(len(values)/(batch*accum))*CONFIG['epochs']
    trace = []; kernels = []
    rng = {'cpu': tensor_hash(torch.get_rng_state()), 'cuda': tensor_hash(torch.cuda.get_rng_state())}
    for step in range(a.steps):
        if time.time() > a.deadline - 45: raise TimeoutError('Diagnostic deadline reached')
        tick = time.time(); indices = order[step*batch*accum:(step+1)*batch*accum]
        optimizer.zero_grad(set_to_none=True)
        progress = step / total; warmup = CONFIG['warmup_fraction']
        scale = min(1., (step+1)/max(1,total*warmup)) if progress < warmup else .1+.9*.5*(1+math.cos(math.pi*(progress-warmup)/(1-warmup)))
        for group in optimizer.param_groups: group['lr'] = CONFIG['learning_rate']*scale
        row = {'step': step+1, 'data_indices': indices.tolist(), 'micro_losses': []}
        for offset in range(0,len(indices),batch):
            subset = indices[offset:offset+batch]; ids = torch.tensor(values[subset],device='cuda')
            first = step == 0 and offset == 0
            with (torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU]) if first else nullcontext()) as prof:
                output = model(input_ids=ids,labels=ids,use_cache=False)
                loss = output.loss
                if not torch.isfinite(loss): raise ValueError('Nonfinite loss')
                if first:
                    row['first_forward_logits_sha256'] = tensor_hash(output.logits[:, -16:, :])
                    row['first_forward_loss_sha256'] = tensor_hash(loss)
                (loss*len(subset)/len(indices)).backward()
                row['micro_losses'].append(float(loss.detach()))
                del output, loss
            if first:
                kernels = sorted({e.key for e in prof.key_averages() if 'attention' in e.key.lower()})
                gradients = snapshot(model,True)
                save_file(gradients,str(out/'first-micro-gradients.safetensors'))
                row['first_micro_gradient_hashes'] = hashes(gradients)
        gradients = snapshot(model,True)
        row['accumulated_gradient_hashes'] = hashes(gradients)
        norm = torch.nn.utils.clip_grad_norm_(parameters,CONFIG['clip_grad_norm'],error_if_nonfinite=True,
                                            foreach=False if POLICIES[a.policy]['explicit_optimizer'] else None)
        row['gradient_norm'] = float(norm)
        row['clipped_gradient_hashes'] = hashes(snapshot(model,True))
        optimizer.step()
        row['parameter_hashes'] = hashes(snapshot(model))
        row['seconds'] = time.time()-tick; trace.append(row)
        with (out/'trace.jsonl').open('a') as f: f.write(json.dumps(row)+'\n')
        print(json.dumps({'policy':a.policy,'attempt':out.name,'step':step+1,'loss':sum(row['micro_losses'])/len(row['micro_losses']),'seconds':row['seconds']}),flush=True)
    torch.cuda.synchronize(); training_seconds = sum(r['seconds'] for r in trace)
    final = snapshot(model); save_file(final,str(out/'final.safetensors'))
    del optimizer, gradients, initial, final
    model.zero_grad(set_to_none=True)
    # Every candidate uses the same evaluation implementation, irrespective of its training backend.
    # Set cuBLAS env only before the process starts: legacy runs retain its default here.
    torch.backends.cuda.enable_flash_sdp(False); torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.backends.cuda.enable_cudnn_sdp(False); torch.backends.cuda.enable_math_sdp(True)
    model.eval(); eval_losses={}; eval_logits=[]
    with torch.inference_mode():
        for domain in ('economics','literature'):
            losses=[]
            for window in data['validation_'+domain][:2]:
                ids=torch.tensor(window[None,:],device='cuda')
                result=model(input_ids=ids,labels=ids,use_cache=False)
                losses.append(float(result.loss));eval_logits.append(result.logits[0,:32].detach().cpu().contiguous())
                del result
            eval_losses[domain]=sum(losses)/len(losses)
    save_file({'logits':torch.cat(eval_logits)},str(out/'evaluation-logits.safetensors'))
    result={'schema':'hypertrix.repeatability-attempt/1','policy':a.policy,'settings':settings,
            'steps':a.steps,'initial_hashes':initial_hashes,'initial_matches_pilot':True,'initial_rng':rng,
            'attention_operations':kernels,'trace':trace,'evaluation_nll':eval_losses,
            'evaluation_scope':'Two frozen validation windows per domain; logits at first 32 positions each. Descriptive, no threshold selection or broad equivalence claim.',
            'training_seconds':training_seconds,'wall_seconds':time.time()-start,
            'packages':{n:importlib.metadata.version(n) for n in ('torch','transformers','peft','numpy')},
            'gpu':torch.cuda.get_device_name(0),'cuda':torch.version.cuda,'cudnn':torch.backends.cudnn.version(),
            'files':{p.name:{'sha256':file_hash(p),'bytes':p.stat().st_size} for p in out.iterdir() if p.is_file()}}
    write_json(out/'result.json',result)
    print(json.dumps({'completed':out.name,'attention_operations':kernels,'wall_seconds':result['wall_seconds']}),flush=True)


def distances(x,y):
    import torch
    error=reference=actual=dot=0.; maximum=0.; exact=0; elements=0
    if x.keys()!=y.keys(): raise ValueError('Tensor names differ')
    for k in x:
        if x[k].shape!=y[k].shape or not torch.isfinite(x[k]).all() or not torch.isfinite(y[k]).all():
            raise ValueError('Invalid tensor shape or nonfinite values')
        a=x[k].double();b=y[k].double();d=b-a
        error+=float(d.square().sum());reference+=float(a.square().sum());actual+=float(b.square().sum());dot+=float((a*b).sum())
        maximum=max(maximum,float(d.abs().max()));exact+=int(torch.equal(x[k],y[k]));elements+=a.numel()
    return {'exact_tensors':exact,'tensor_count':len(x),'max_abs':maximum,'rms':math.sqrt(error/max(1,elements)),
            'reference_l2':math.sqrt(reference),'difference_l2':math.sqrt(error),
            'relative_l2':math.sqrt(error/reference) if reference else (0. if error==0 else None),
            'cosine':dot/math.sqrt(reference*actual) if reference and actual else None}


def compare(left,right):
    import torch
    from safetensors.torch import load_file
    torch.set_num_threads(4)
    one=read_json(left/'result.json');two=read_json(right/'result.json')
    a=load_file(str(left/'final.safetensors'));b=load_file(str(right/'final.safetensors'))
    initial=load_file(str(left/'initial.safetensors'))
    factors=distances(a,b)
    changes=distances({k:a[k]-initial[k] for k in a},{k:b[k]-initial[k] for k in b})
    # Form each effective delta in FP64 on CPU, independent of a serving merge.
    # Keep one map at a time: all dense updates together would be much larger than LoRA.
    maps=[];error=reference=actual=dot=0.;maximum=0.
    for name in sorted(k for k in a if '.lora_A.' in k):
        partner=name.replace('.lora_A.','.lora_B.')
        x=2*(a[partner].double()@a[name].double());y=2*(b[partner].double()@b[name].double());d=y-x
        e=float(d.square().sum());r=float(x.square().sum());q=float(y.square().sum());p=float((x*y).sum())
        mx=float(d.abs().max());error+=e;reference+=r;actual+=q;dot+=p;maximum=max(maximum,mx)
        maps.append({'map':name.split('.lora_A.')[0],'relative_l2':math.sqrt(e/r) if r else None,'max_abs':mx})
    effective={'map_count':len(maps),'relative_l2':math.sqrt(error/reference) if reference else None,
               'cosine':dot/math.sqrt(reference*actual) if reference and actual else None,'max_abs':maximum,
               'difference_l2':math.sqrt(error),'reference_l2':math.sqrt(reference),
               'worst_maps':sorted(maps,key=lambda r:r['relative_l2'] or 0,reverse=True)[:8]}
    x=load_file(str(left/'evaluation-logits.safetensors'))['logits'].double()
    y=load_file(str(right/'evaluation-logits.safetensors'))['logits'].double()
    lp=x.log_softmax(-1);lq=y.log_softmax(-1);kl=(lp.exp()*(lp-lq)).sum(-1)
    first=None
    for r,s in zip(one['trace'],two['trace']):
        for phase in ('first_forward_loss_sha256','first_forward_logits_sha256','first_micro_gradient_hashes','accumulated_gradient_hashes','gradient_norm','clipped_gradient_hashes','parameter_hashes'):
            if r.get(phase)!=s.get(phase):
                first={'step':r['step'],'phase':phase};break
        if first:break
    return {'left':left.name,'right':right.name,'initial_tensors_equal':one['initial_hashes']==two['initial_hashes'],
            'initial_rng_equal':one['initial_rng']==two['initial_rng'],'first_observed_difference':first,
            'adapter_factors':factors,'adapter_change_from_initial':changes,'effective_lora_delta':effective,
            'behavior':{'positions':len(x),'top1_agreement':float((x.argmax(-1)==y.argmax(-1)).double().mean()),
                        'mean_kl_nats':float(kl.mean()),'max_kl_nats':float(kl.max()),
                        'nll_difference':{d:two['evaluation_nll'][d]-one['evaluation_nll'][d] for d in one['evaluation_nll']}},
            'first_micro_gradients':distances(load_file(str(left/'first-micro-gradients.safetensors')),load_file(str(right/'first-micro-gradients.safetensors')))}


def matrix(a):
    started=time.time();deadline=started+1200
    write_json(a.out/'gpu-session.json',{'started_unix':started,'deadline_unix':deadline,
               'scope':'New bounded repeatability diagnostic; does not extend or replace the original pilot.'})
    if read_json(a.source/'recipe.json')['attention']!='sdpa':raise ValueError('Unexpected source recipe')
    from dawl_finetune.run import CONFIG
    if read_json(a.source/'recipe.json')!=CONFIG:raise ValueError('Frozen image recipe mismatch')
    results=[];pairs=[]
    for policy,steps in [('legacy-auto',4),('math-only',4),('strict-auto',4),('strict-math',12)]:
        success=[]
        for attempt in (1,2):
            folder=a.out/f'{policy}-{attempt}'
            env=dict(os.environ);env['PYTHONHASHSEED']='20261014';env.pop('CUBLAS_WORKSPACE_CONFIG',None)
            if POLICIES[policy]['strict']:env['CUBLAS_WORKSPACE_CONFIG']=':4096:8'
            command=[sys.executable,'-P',str(Path(__file__).resolve()),'worker','--source',str(a.source),
                     '--out',str(folder),'--policy',policy,'--steps',str(steps),'--deadline',str(deadline)]
            with (a.out/f'{folder.name}.log').open('x') as log:
                try:
                    proc=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,
                                        timeout=max(1,deadline-time.time()-30))
                    code=proc.returncode
                except subprocess.TimeoutExpired:code=124
            row={'attempt':folder.name,'returncode':code,'result':str(folder/'result.json') if (folder/'result.json').exists() else None}
            results.append(row);print(json.dumps(row),flush=True)
            write_json(a.out/f'{folder.name}-exit.json',row)
            if code==0:success.append(folder)
            if time.time()>deadline-60:break
        if len(success)==2:
            pair=compare(*success);pairs.append({'policy':policy,**pair})
            write_json(a.out/f'{policy}-comparison.json',pair)
            print(json.dumps({'policy':policy,'exact':pair['adapter_factors']['exact_tensors'],
                              'first_difference':pair['first_observed_difference'],'effective_relative_l2':pair['effective_lora_delta']['relative_l2']}),flush=True)
        if time.time()>deadline-60:break
    result={'schema':'hypertrix.repeatability-matrix/1','completed_utc':datetime.now(timezone.utc).isoformat(),
            'attempts':results,'comparisons':pairs,'wall_seconds':time.time()-started,
            'source_recipe_sha256':file_hash(a.source/'recipe.json'),'source_dataset_lock_sha256':file_hash(a.source/'dataset-lock.json'),
            'source_lineage_head':json.loads((a.source/'lineage.jsonl').read_text().splitlines()[-1])['id'],
            'source_replay_status':read_json(a.source/'replay-verification.json')['status'],
            'files':{p.relative_to(a.out).as_posix():{'sha256':file_hash(p),'bytes':p.stat().st_size}
                     for p in sorted(a.out.rglob('*')) if p.is_file()},
            'scope':'Short same-host fresh-process diagnostics with saved candidate weights, gradients and outputs. No full-epoch, cross-hardware, semantic correctness, or adversarial-work proof is claimed.'}
    write_json(a.out/'replay-verification.json',result)
    receipt=sign({'schema':'hypertrix.repeatability-attestation/1','result_sha256':file_hash(a.out/'replay-verification.json'),
                  'source_lineage_head':result['source_lineage_head']},load_key(a.source/'operator.private.pem'))
    write_json(a.out/'attestation.json',receipt)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['matrix','worker'])
    p.add_argument('--source',type=Path,default=Path('/source'));p.add_argument('--out',type=Path,default=Path('/experiment'))
    p.add_argument('--policy',choices=list(POLICIES));p.add_argument('--steps',type=int,default=4)
    p.add_argument('--deadline',type=float,default=0);args=p.parse_args()
    try:globals()[args.command](args)
    except Exception:
        traceback.print_exc();raise
