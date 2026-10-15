"""Bounded scratch training, immutable checkpoints and fresh-process replay."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
import signal
from pathlib import Path
import time
import numpy as np
import torch
from torch.nn.attention import sdpa_kernel, SDPBackend
from safetensors.torch import load_file, save_file
from tokenizers import Tokenizer
from dawl_node.common import canonical_json, keygen, load_key, sign, sha256, write_json, verify_signature, immutable_write
from .data import file_hash, load, prepare
from .model import Config, LanguageModel, upcycle, parameter_counts
from .progress import progress, remaining


def configure(device, precision="bf16", attention="math"):
    if precision not in ("fp32","bf16"): raise ValueError("Unsupported precision")
    if attention not in ('math','flash') or (attention=='flash' and precision!='bf16'):raise ValueError('Flash training requires BF16')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.enable_flash_sdp(attention=='flash')
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.backends.cuda.enable_cudnn_sdp(False)
    torch.backends.cuda.enable_math_sdp(True)
    if device == 'cuda' and not torch.cuda.is_available(): raise ValueError('CUDA requested but unavailable')
    return {'device': device, 'torch': torch.__version__, 'cuda': torch.version.cuda,
            'gpu': torch.cuda.get_device_name(0) if device == 'cuda' else None,
            'precision': precision, 'attention': attention, 'deterministic_algorithms': True, 'tf32': False,
            'threads': 4, 'numpy': np.__version__, 'tokenizers': importlib.metadata.version('tokenizers'),
            'safetensors': importlib.metadata.version('safetensors')}


def event(root, kind, **values):
    row = {'event': kind, 'utc': datetime.now(timezone.utc).isoformat(), **values}
    with (root/'events.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, allow_nan=False)+'\n')
    print(json.dumps(row, allow_nan=False), flush=True)


def attest(root, kind, payload):
    path = root/'lineage.jsonl'
    records = path.read_text().splitlines() if path.exists() else []
    record = sign({'schema': 'hypertrix.scratch-record/1', 'sequence': len(records),
        'previous': json.loads(records[-1])['id'] if records else None, 'kind': kind, 'payload': payload},
        load_key(root/'operator.private.pem'))
    with path.open('a') as stream: stream.write(canonical_json(record).decode()+'\n')
    return record['id']


def validate_lineage(root):
    public = (root/'operator.public.key').read_text().strip()
    previous, bodies = None, []
    for index, line in enumerate((root/'lineage.jsonl').read_text().splitlines()):
        record = json.loads(line)
        body = verify_signature(record, public)
        if body['sequence'] != index or body['previous'] != previous: raise ValueError('Broken lineage chain')
        previous = record['id']; bodies.append(body)
    return bodies


def save_checkpoint(root, model, optimizer, step, evaluation=None):
    directory = root/'checkpoints'/f'{step:07d}'
    directory.mkdir(parents=True, exist_ok=False)
    tensors = {'model.'+name: value.detach().cpu().contiguous() for name,value in model.state_dict().items()}
    for name, parameter in model.named_parameters():
        for key, value in optimizer.state.get(parameter, {}).items():
            tensors['optimizer.'+name+'.'+key] = value.detach().cpu().contiguous()
    save_file(tensors, str(directory/'state.safetensors'))
    desc = {'schema': 'hypertrix.scratch-checkpoint/1', 'step': step,
        'state_sha256': file_hash(directory/'state.safetensors'), 'config_sha256': file_hash(root/'config.json'),
        'recipe_sha256': file_hash(root/'recipe.json'), 'evaluation': evaluation}
    write_json(directory/'checkpoint.json', desc)
    attest(root, 'checkpoint', {'path': f'checkpoints/{step:07d}/checkpoint.json',
                              'sha256': file_hash(directory/'checkpoint.json'), **desc})
    return desc


def load_checkpoint(root, step, device='cpu', with_optimizer=False):
    records = validate_lineage(root)
    path = root/'checkpoints'/f'{step:07d}'
    desc = json.loads((path/'checkpoint.json').read_bytes())
    if not any(r['kind'] == 'checkpoint' and r['payload']['sha256'] == file_hash(path/'checkpoint.json') for r in records):
        raise ValueError('Checkpoint is not in the signed lineage')
    if file_hash(path/'state.safetensors') != desc['state_sha256']: raise ValueError('Checkpoint bytes differ')
    if file_hash(root/'config.json') != desc['config_sha256'] or file_hash(root/'recipe.json') != desc['recipe_sha256']:
        raise ValueError('Model recipe differs')
    config = Config(**json.loads((root/'config.json').read_bytes()))
    model = LanguageModel(config).to(device)
    tensors = load_file(str(path/'state.safetensors'), device='cpu')
    model.load_state_dict({k.removeprefix('model.'): v for k,v in tensors.items() if k.startswith('model.')}, strict=True)
    recipe = json.loads((root/'recipe.json').read_bytes())
    optimizer = make_optimizer(model, recipe) if with_optimizer else None
    if optimizer is not None:
        for name, parameter in model.named_parameters():
            prefix = 'optimizer.'+name+'.'
            state = {key.removeprefix(prefix): value.to('cpu' if key.endswith('.step') else device)
                     for key,value in tensors.items() if key.startswith(prefix)}
            if state: optimizer.state[parameter] = state
    return model, optimizer, desc


def make_optimizer(model, recipe):
    return torch.optim.AdamW(model.parameters(), lr=recipe['learning_rate'], betas=(.9,.95),
                            weight_decay=.1, foreach=False, fused=False)


def batch_indices(step, count, recipe, cache):
    per_epoch = math.ceil(count/recipe['batch_size'])
    epoch, batch = divmod(step, per_epoch)
    if cache.get('epoch') != epoch:
        cache['order'] = np.random.default_rng(recipe['seed']+epoch).permutation(count)
        cache['epoch'] = epoch
    start = batch*recipe['batch_size']
    return cache['order'][start:start+recipe['batch_size']]


def batch_tensor(values, indices, pad, device):
    batch = torch.tensor(np.asarray(values[indices], dtype=np.int64), device=device)
    labels = batch[:,1:].clone()
    labels[labels == pad] = -100
    return batch[:,:-1], labels


def update(model, optimizer, values, indices, recipe, step, device):
    model.train(); optimizer.zero_grad(set_to_none=True)
    x,y = batch_tensor(values, indices, recipe['pad_id'], device)
    backend=SDPBackend.FLASH_ATTENTION if recipe['runtime'].get('attention')=='flash' else SDPBackend.MATH
    with sdpa_kernel(backend), torch.autocast(device_type=device, dtype=torch.bfloat16, enabled=recipe['runtime']['precision']=='bf16'):
        _, loss, balance, utilization = model(x,y)
        objective = loss + recipe['router_balance_weight']*balance
    if not torch.isfinite(objective): raise ValueError('Nonfinite training objective')
    objective.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, foreach=False, error_if_nonfinite=True)
    warmup = max(1, recipe['max_steps']//20)
    fraction = min(1, (step+1)/warmup) if step < warmup else .1+.9*.5*(1+math.cos(math.pi*(step-warmup)/max(1,recipe['max_steps']-warmup)))
    for group in optimizer.param_groups: group['lr'] = recipe['learning_rate']*fraction
    optimizer.step()
    return {'loss': loss.item(), 'auxiliary': balance.item(), 'tokens': int((y!=-100).sum().item()),
            'expert_tokens': [u.cpu().tolist() for u in utilization]}


@torch.inference_mode()
def evaluate(model, arrays, blueprint, device, window_index, split='validation', per_domain=32):
    model.eval(); output = {}
    for domain in sorted({row['domain'] for row in window_index}):
        indices = [i for row in window_index if row['split']==split and row['domain']==domain
                   for i in range(row['start_row'],row['end_row'])]
        if not indices: continue
        # A frozen spread over the entire domain, rather than the first source only.
        indices = np.asarray(blueprint['_evaluation'][split][domain]) if '_evaluation' in blueprint else np.asarray(indices)[np.linspace(0,len(indices)-1,min(per_domain,len(indices)),dtype=int)]
        total, count = 0., 0
        for start in range(0,len(indices),4):
            x,y = batch_tensor(arrays[split],indices[start:start+4],blueprint['pad_id'],device)
            _,loss,_,_ = model(x,y)
            n = int((y!=-100).sum()); total += loss.item()*n; count += n
        nll = total/count
        output[domain] = {'nll': nll, 'perplexity': math.exp(nll), 'targets': count, 'windows': len(indices)}
    output['mean_domain_nll'] = sum(v['nll'] for v in output.values())/len(output)
    return output


def create(root, dataset, args):
    blueprint,_ = load(dataset)
    root.mkdir(parents=True, exist_ok=False)
    runtime = configure(args.device,args.precision,args.attention)
    torch.manual_seed(args.seed)
    config = Config(vocab_size=blueprint['vocab_size'], context=blueprint['context'])
    parent = None
    if args.parent:
        parent_summary = json.loads((args.parent/'summary.json').read_bytes())
        parent_recipe = json.loads((args.parent/'recipe.json').read_bytes())
        if parent_recipe['blueprint_sha256'] != file_hash(dataset/'blueprint.json'):
            raise ValueError('Upcycling requires the same tokenizer and Blueprint')
        dense,_,_ = load_checkpoint(args.parent,parent_summary['selected_step'],args.device)
        model = upcycle(dense,4,2)
        config = model.config
        parent = {'checkpoint_sha256': file_hash(args.parent/'checkpoints'/f'{parent_summary["selected_step"]:07d}'/'checkpoint.json'),
                  'operator': (args.parent/'operator.public.key').read_text().strip(),
                  'operation': 'Copy each odd-layer dense FFN into four experts; seeded router; fresh AdamW state.'}
    else: model = LanguageModel(config).to(args.device)
    max_steps = math.ceil(blueprint['arrays']['train']['windows']/args.batch_size)*args.epochs
    if args.max_steps is not None:
        if args.max_steps<1:raise ValueError('max-steps must be positive')
        max_steps=min(max_steps,args.max_steps)
    recipe = {'schema': 'hypertrix.scratch-recipe/1', 'blueprint_sha256': file_hash(dataset/'blueprint.json'),
        'seed': args.seed, 'epochs': args.epochs, 'batch_size': args.batch_size, 'max_steps': max_steps,
        'pad_id': blueprint['pad_id'], 'learning_rate': args.learning_rate,
        'router_balance_weight': .01, 'checkpoint_every': args.checkpoint_every,
        'budget_seconds': args.budget_seconds, 'selection': 'Minimum equal-domain validation NLL; test only initial and selected.',
        'initialization': 'Random normal std .02, zero linear biases; tied embeddings/head; RMSNorm eps=1e-6; SwiGLU FFN.' if parent is None else parent,
        'optimizer': 'AdamW beta=(.9,.95), weight_decay=.1, clip=1, no fused/foreach; 5% warmup then cosine to 10%.',
        'data_order': 'NumPy default_rng(seed+epoch) permutation of all training windows; each epoch includes final short batch.',
        'runtime': runtime, 'scope': 'Single trainer local experimental lineage; no Oracle or multi-host training claim.'}
    write_json(root/'config.json',asdict(config)); write_json(root/'recipe.json',recipe)
    # Bind the exact local implementation, including data and model code.
    implementation = {p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')}
    write_json(root/'implementation.json',implementation)
    for name in implementation:immutable_write(root/'implementation'/name,(Path(__file__).parent/name).read_bytes())
    dependencies={name:file_hash(Path(__file__).parents[1]/name) for name in
                  ('dawl_scratch/data.py','dawl_finetune/data.py','dawl_node/common.py')}
    write_json(root/'dependencies.json',dependencies)
    for name in dependencies:immutable_write(root/'dependencies'/name,(Path(__file__).parents[1]/name).read_bytes())
    keygen(root/'operator.private.pem',root/'operator.public.key')
    attest(root,'initialization',{'config_sha256':file_hash(root/'config.json'), 'recipe_sha256':file_hash(root/'recipe.json'),
        'implementation_sha256':file_hash(root/'implementation.json'), 'dependencies_sha256':file_hash(root/'dependencies.json'), 'parent':parent})
    save_checkpoint(root,model,make_optimizer(model,recipe),0)
    return recipe


def train(root, dataset, device, resume=False):
    stopped=[False]
    def stop_after_update(signum,frame):stopped[0]=True
    signal.signal(signal.SIGTERM,stop_after_update)
    signal.signal(signal.SIGINT,stop_after_update)
    recipe = json.loads((root/'recipe.json').read_bytes())
    runtime = configure(device,recipe['runtime']['precision'],recipe['runtime']['attention'])
    blueprint,arrays = load(dataset)
    blueprint['_evaluation']=json.loads((dataset/'evaluation.json').read_bytes())
    if recipe['runtime'] != runtime: raise ValueError('Execution runtime changed')
    if recipe['blueprint_sha256'] != file_hash(dataset/'blueprint.json'): raise ValueError('Dataset changed')
    check_implementation(root)
    available = sorted(int(p.name) for p in (root/'checkpoints').iterdir() if (p/'checkpoint.json').exists())
    start = available[-1] if resume else 0
    if start and not resume: raise ValueError('Use resume')
    model,optimizer,_ = load_checkpoint(root,start,device,True)
    window_index = json.loads((dataset/'windows.json').read_bytes())
    baseline = evaluate(model,arrays,blueprint,device,window_index)
    if start == 0:
        write_json(root/'baseline.json',{'validation':baseline,'test':evaluate(model,arrays,blueprint,device,window_index,'test')})
    best, selected = baseline['mean_domain_nll'], start
    for existing in available:
        desc = json.loads((root/'checkpoints'/f'{existing:07d}'/'checkpoint.json').read_bytes())
        if desc.get('evaluation') and desc['evaluation']['mean_domain_nll'] < best:
            best,selected = desc['evaluation']['mean_domain_nll'],existing
    cache, tokens, began = {}, 0, time.monotonic()
    last_saved, step = start,start
    event(root,'training-started',start_step=start,parameters=sum(p.numel() for p in model.parameters()),max_steps=recipe['max_steps'])
    for step in range(start,recipe['max_steps']):
        if stopped[0] or time.monotonic()-began > recipe['budget_seconds'] or remaining()<300: break
        indices = batch_indices(step,len(arrays['train']),recipe,cache)
        result = update(model,optimizer,arrays['train'],indices,recipe,step,device)
        tokens += result['tokens']
        completed = step+1
        if completed % 10 == 0:
            event(root,'progress',step=completed,**result,elapsed_seconds=time.monotonic()-began,
                  target_tokens_this_session=tokens,targets_per_second=tokens/(time.monotonic()-began))
        if completed % 10 == 0:
            progress(root,'Training weights',job='Seed II · 40.6M',role='model-trainer',status='running',
                     completed=completed,total=recipe['max_steps'],unit='steps',step=completed,loss=result['loss'],
                     epoch=completed/math.ceil(len(arrays['train'])/recipe['batch_size']),epochs=recipe['epochs'],
                     target_tokens=tokens,targets_per_second=tokens/(time.monotonic()-began),elapsed_seconds=time.monotonic()-began,
                     checkpoint_step=last_saved,selected_step=selected,validation_nll=best,parameters=sum(p.numel() for p in model.parameters()))
        if completed % recipe['checkpoint_every'] == 0 or completed == recipe['max_steps']:
            evaluation = evaluate(model,arrays,blueprint,device,window_index)
            save_checkpoint(root,model,optimizer,completed,evaluation); last_saved=completed
            if evaluation['mean_domain_nll'] < best: best,selected=evaluation['mean_domain_nll'],completed
            event(root,'checkpoint',step=completed,selected_step=selected,validation=evaluation)
        step = completed
    if step != last_saved:
        evaluation=evaluate(model,arrays,blueprint,device,window_index)
        save_checkpoint(root,model,optimizer,step,evaluation)
        if evaluation['mean_domain_nll'] < best: best,selected=evaluation['mean_domain_nll'],step
    selected_model,_,_=load_checkpoint(root,selected,device)
    test=evaluate(selected_model,arrays,blueprint,device,window_index,'test')
    summary={'schema':'hypertrix.scratch-result/1','completed_steps':step,'planned_steps':recipe['max_steps'],
        'selected_step':selected,'target_tokens_this_session':tokens,'elapsed_seconds':time.monotonic()-began,
        'validation_mean_nll':best,'selected_test':test,'status':'complete' if step==recipe['max_steps'] else 'stopped' if stopped[0] else 'budget-stopped',
        'parameters':sum(p.numel() for p in model.parameters()),'config':asdict(model.config),
        'blueprint_sha256':file_hash(dataset/'blueprint.json')}
    # Repeated resume sessions create new immutable summaries; pointer is a convenience only.
    write_json(root/f'summary-{step:07d}.json',summary)
    attest(root,'training-result',{'sha256':file_hash(root/f'summary-{step:07d}.json'),**summary})
    temporary=root/'summary.pending'; temporary.write_bytes(canonical_json(summary)); temporary.replace(root/'summary.json')
    progress(root,'Training complete',job='Seed II · 40.6M',role='model-trainer',status=summary['status'],
             completed=step,total=recipe['max_steps'],unit='steps',selected_step=selected,validation_nll=best,**{k:summary[k] for k in ('elapsed_seconds','parameters')})
    event(root,'training-finished',**summary)
    return summary


def check_implementation(root):
    expected=json.loads((root/'implementation.json').read_bytes())
    if any(file_hash(Path(__file__).parent/name)!=digest for name,digest in expected.items()):
        raise ValueError('Training implementation changed')
    dependencies=json.loads((root/'dependencies.json').read_bytes())
    if any(file_hash(Path(__file__).parents[1]/name)!=digest for name,digest in dependencies.items()):raise ValueError('Training dependency changed')
    initialization=validate_lineage(root)[0]['payload']
    if initialization['implementation_sha256']!=file_hash(root/'implementation.json') or initialization['dependencies_sha256']!=file_hash(root/'dependencies.json'):
        raise ValueError('Implementation manifest differs from signed initialization')


def replay(root,dataset,device,start,end):
    recipe=json.loads((root/'recipe.json').read_bytes())
    runtime=configure(device,recipe['runtime']['precision'],recipe['runtime']['attention']);check_implementation(root)
    blueprint,arrays=load(dataset)
    if runtime!=recipe['runtime'] or file_hash(dataset/'blueprint.json')!=recipe['blueprint_sha256']:
        raise ValueError('Replay runtime or Blueprint differs')
    if not 0<=start<end<=recipe['max_steps']: raise ValueError('Invalid replay interval')
    model,optimizer,_=load_checkpoint(root,start,device,True)
    expected,expected_optimizer,_=load_checkpoint(root,end,'cpu',True)
    cache={};began=time.monotonic()
    for step in range(start,end):
        update(model,optimizer,arrays['train'],batch_indices(step,len(arrays['train']),recipe,cache),recipe,step,device)
        if (step-start)%10==0:progress(root,'Replaying training',job='Seed II replay',role='model-replay',status='running',
            completed=step-start+1,total=end-start,unit='steps',start_step=start,end_step=end,elapsed_seconds=time.monotonic()-began)
    differences=[]
    actual=model.state_dict();target=expected.state_dict()
    for name in target:
        a,b=actual[name].detach().cpu(),target[name]
        differences.append({'name':name,'exact':torch.equal(a,b),'max_absolute':float((a-b).abs().max())})
    optimizer_exact=True
    for (_,a),(_,b) in zip(model.named_parameters(),expected.named_parameters()):
        sa,sb=optimizer.state.get(a,{}),expected_optimizer.state.get(b,{})
        if sa.keys()!=sb.keys() or any(not torch.equal(sa[k].cpu(),sb[k].cpu()) for k in sa): optimizer_exact=False
    result={'schema':'hypertrix.scratch-replay/1','start_step':start,'end_step':end,
        'tensor_count':len(differences),'exact_tensors':sum(d['exact'] for d in differences),
        'max_absolute':max(d['max_absolute'] for d in differences),'optimizer_exact':optimizer_exact,
        'accepted':all(d['exact'] for d in differences) and optimizer_exact,
        'elapsed_seconds':time.monotonic()-began,'checks':differences,
        'scope':'Fresh process, same physical GPU and pinned runtime; only the stated interval was replayed.'}
    path=root/f'replay-{start:07d}-{end:07d}.json';write_json(path,result)
    attest(root,'replay',{'sha256':file_hash(path),**{k:v for k,v in result.items() if k!='checks'}})
    progress(root,'Replay verified' if result['accepted'] else 'Replay mismatch',job='Seed II replay',role='model-replay',
             status='complete' if result['accepted'] else 'failed',completed=end-start,total=end-start,unit='steps',
             start_step=start,end_step=end,exact_tensors=result['exact_tensors'],tensor_count=result['tensor_count'])
    event(root,'replay-finished',**{k:v for k,v in result.items() if k!='checks'})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','train','replay'])
    p.add_argument('--root',type=Path,required=True);p.add_argument('--dataset',type=Path)
    p.add_argument('--books',type=Path);p.add_argument('--code',type=Path)
    p.add_argument('--precision',choices=['fp32','bf16'],default='bf16')
    p.add_argument('--attention',choices=['math','flash'],default='math')
    p.add_argument('--max-steps',type=int)
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda');p.add_argument('--seed',type=int,default=20261014)
    p.add_argument('--epochs',type=int,default=3);p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--learning-rate',type=float,default=.0006);p.add_argument('--checkpoint-every',type=int,default=250)
    p.add_argument('--budget-seconds',type=int,default=3600);p.add_argument('--parent',type=Path)
    p.add_argument('--resume',action='store_true');p.add_argument('--start',type=int,default=0);p.add_argument('--end',type=int)
    a=p.parse_args()
    if a.command=='prepare': print(json.dumps(prepare(a.root,a.books,a.code)),flush=True)
    elif a.command=='train':
        if not a.resume:create(a.root,a.dataset,a)
        result=train(a.root,a.dataset,a.device,a.resume)
        if result['status']=='stopped':raise SystemExit(130)
    else:
        result=replay(a.root,a.dataset,a.device,a.start,a.end)
        if not result['accepted']:raise SystemExit(1)


if __name__=='__main__':main()
