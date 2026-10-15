"""Measured scratch pre-training, signed checkpoints, exact replay and LAN DDP.

Oracle's existing adapter certificates are deliberately not reused for these
full-model artifacts. This module records an explicitly scoped lab lineage.
"""
import argparse
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime,timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import threading
import time
import numpy as np
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel as DDP
from torch.nn.attention import sdpa_kernel,SDPBackend
from safetensors.torch import save_file,load_file
from dawl_node.common import canonical_json,write_json,immutable_write,keygen,load_key,sign,verify_signature,sha256
from dawl_scratch.data import file_hash
from dawl_seed.progress import progress
from .data import prepare,load,schedule
from .model import Config,LanguageModel


def configure(device,precision='bf16',attention='flash',threads=4):
    if precision not in ('fp32','bf16') or attention not in ('math','flash'):raise ValueError('Unsupported runtime')
    if attention=='flash' and (precision!='bf16' or device!='cuda'):raise ValueError('Flash requires BF16 CUDA')
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
    torch.set_num_threads(threads);torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
    torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    torch.backends.cuda.enable_flash_sdp(attention=='flash');torch.backends.cuda.enable_math_sdp(True)
    torch.backends.cuda.enable_mem_efficient_sdp(False);torch.backends.cuda.enable_cudnn_sdp(False)
    if device=='cuda' and not torch.cuda.is_available():raise ValueError('CUDA unavailable')
    return {'device':device,'torch':str(torch.__version__),'cuda':torch.version.cuda,
        'gpu':torch.cuda.get_device_name() if device=='cuda' else None,
        'capability':list(torch.cuda.get_device_capability()) if device=='cuda' else None,
        'precision':precision,'attention':attention,'deterministic':True,'tf32':False,'threads':threads,
        'numpy':np.__version__,'tokenizers':importlib.metadata.version('tokenizers'),'safetensors':importlib.metadata.version('safetensors')}


def event(root,kind,**values):
    row={'event':kind,'utc':datetime.now(timezone.utc).isoformat(),**values}
    with (root/'events.jsonl').open('a') as f:f.write(json.dumps(row,allow_nan=False)+'\n')
    print(json.dumps(row,allow_nan=False),flush=True)


def attest(root,kind,payload):
    path=root/'lineage.jsonl';lines=path.read_text().splitlines() if path.exists() else []
    row=sign({'schema':'hypertrix.lab-lineage/1','sequence':len(lines),'previous':json.loads(lines[-1])['id'] if lines else None,
              'kind':kind,'payload':payload},load_key(root/'operator.private.pem'))
    with path.open('a') as f:f.write(canonical_json(row).decode()+'\n')
    return row['id']


def validate_lineage(root,expected_public=None):
    public=(root/'operator.public.key').read_text().strip()
    if expected_public and public!=expected_public:raise ValueError('Unexpected signer')
    previous=None;result=[]
    for i,line in enumerate((root/'lineage.jsonl').read_text().splitlines()):
        envelope=json.loads(line);body=verify_signature(envelope,public)
        if body['schema']!='hypertrix.lab-lineage/1' or body['sequence']!=i or body['previous']!=previous:raise ValueError('Broken lineage')
        previous=envelope['id'];result.append(body)
    if not result or result[0]['kind']!='initialization':raise ValueError('Missing initialization')
    return result


def implementation():
    base=Path(__file__).parents[1]
    paths=list(Path(__file__).parent.glob('*.py'))+[base/'dawl_node/common.py',base/'data_ingestion/protocol.py',base/'dawl_scratch/data.py',base/'dawl_finetune/data.py',base/'dawl_seed/progress.py']
    return {str(p.relative_to(base)).replace('\\','/'):file_hash(p) for p in paths}


def check_implementation(root):
    expected=json.loads((root/'implementation.json').read_bytes())
    base=Path(__file__).parents[1]
    if any(file_hash(base/name)!=digest for name,digest in expected.items()):raise ValueError('Pinned training implementation differs')
    first=validate_lineage(root)[0]['payload']
    for name,key in [('implementation.json','implementation_sha256'),('recipe.json','recipe_sha256'),('config.json','config_sha256'),('schedule.json','schedule_manifest_sha256')]:
        if file_hash(root/name)!=first[key]:raise ValueError('Initialization commitment differs: '+name)
    if file_hash(root/'schedule.npy')!=json.loads((root/'schedule.json').read_bytes())['sha256']:raise ValueError('Schedule differs')


def optimizer_for(model,recipe):
    return torch.optim.AdamW(model.parameters(),lr=recipe['learning_rate'],betas=(.9,.95),weight_decay=.1,foreach=False,fused=False)


def save_checkpoint(root,model,optimizer,step,evaluation=None):
    path=root/'checkpoints'/f'{step:07d}'
    if path.exists():raise ValueError('Checkpoint already exists')
    # Only complete directories are discoverable. Interrupted writes remain as
    # uniquely named staging directories and never replace accepted evidence.
    import uuid
    staging=path.parent/('.pending-'+uuid.uuid4().hex);staging.mkdir(parents=True)
    tensors={'model.'+k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()}
    for name,param in model.named_parameters():
        for k,v in optimizer.state.get(param,{}).items():tensors['optimizer.'+name+'.'+k]=v.detach().cpu().contiguous()
    save_file(tensors,str(staging/'state.safetensors'))
    desc={'schema':'hypertrix.lab-checkpoint/1','step':step,'state_sha256':file_hash(staging/'state.safetensors'),
        'config_sha256':file_hash(root/'config.json'),'recipe_sha256':file_hash(root/'recipe.json'),'evaluation':evaluation}
    write_json(staging/'checkpoint.json',desc);staging.rename(path)
    attest(root,'checkpoint',{'descriptor_sha256':file_hash(path/'checkpoint.json'),**desc});return desc


def load_checkpoint(root,step,device='cpu',with_optimizer=False):
    root=Path(root);records=validate_lineage(root);path=root/'checkpoints'/f'{step:07d}'
    desc=json.loads((path/'checkpoint.json').read_bytes())
    if not any(r['kind']=='checkpoint' and r['payload']['descriptor_sha256']==file_hash(path/'checkpoint.json') for r in records):raise ValueError('Unsigned checkpoint')
    for name,key in [('config.json','config_sha256'),('recipe.json','recipe_sha256')]:
        if file_hash(root/name)!=desc[key]:raise ValueError('Checkpoint recipe differs')
    if file_hash(path/'state.safetensors')!=desc['state_sha256']:raise ValueError('Checkpoint tensor bytes differ')
    model=LanguageModel(Config(**json.loads((root/'config.json').read_bytes()))).to(device)
    tensors=load_file(str(path/'state.safetensors'))
    model.load_state_dict({k[6:]:v for k,v in tensors.items() if k.startswith('model.')},strict=True)
    opt=optimizer_for(model,json.loads((root/'recipe.json').read_bytes())) if with_optimizer else None
    if opt is not None:
        for name,param in model.named_parameters():
            prefix='optimizer.'+name+'.'
            state={k[len(prefix):]:v.to('cpu' if k.endswith('.step') else device) for k,v in tensors.items() if k.startswith(prefix)}
            if state:opt.state[param]=state
    return model,opt,desc


def batch_tensor(values,ids,pad,device):
    batch=torch.tensor(np.asarray(values[ids],dtype=np.int64),device=device)
    y=batch[:,1:].clone();y[y==pad]=-100
    return batch[:,:-1],y


def update(model,opt,values,indices,recipe,step,device,rank=0,world=1):
    """Token-weighted gradient accumulation; DDP shards one global frozen batch."""
    model.train();opt.zero_grad(set_to_none=True)
    if len(indices)%world:raise ValueError('Global batch must divide across ranks')
    local=indices[rank::world]
    global_targets=int((np.asarray(values[indices,1:])!=recipe['pad_id']).sum())
    total_loss=0.;micro=recipe['micro_batch'];count=0
    for at in range(0,len(local),micro):
        part=local[at:at+micro];x,y=batch_tensor(values,part,recipe['pad_id'],device);n=int((y!=-100).sum())
        sync=(at+micro>=len(local));guard=nullcontext() if world==1 or sync else model.no_sync()
        backend=SDPBackend.FLASH_ATTENTION if recipe['runtime']['attention']=='flash' else SDPBackend.MATH
        with guard,sdpa_kernel(backend),torch.autocast(device_type=device,dtype=torch.bfloat16,enabled=recipe['runtime']['precision']=='bf16'):
            _,loss,_=model(x,y)
            if not torch.isfinite(loss):raise ValueError('Nonfinite training loss')
            objective=loss*(n*world/global_targets)
            objective.backward()
        total_loss+=float(loss.detach())*n;count+=n
    gradient=float(torch.nn.utils.clip_grad_norm_(model.parameters(),1.,foreach=False,error_if_nonfinite=True))
    warmup=max(1,recipe['steps']//20)
    scale=(step+1)/warmup if step<warmup else .1+.9*.5*(1+math.cos(math.pi*(step-warmup)/max(1,recipe['steps']-warmup)))
    for group in opt.param_groups:group['lr']=recipe['learning_rate']*scale
    opt.step()
    local_targets=int(count)
    if world>1:
        totals=torch.tensor([total_loss,count],dtype=torch.float64,device=device);dist.all_reduce(totals);total_loss,count=totals.tolist()
    return {'loss':total_loss/count,'target_tokens':global_targets,'local_target_tokens':local_targets,'gradient_norm':gradient,'learning_rate':recipe['learning_rate']*scale}


@torch.inference_mode()
def evaluate(model,arrays,bp,evaluation,device,split='validation'):
    model.eval();result={}
    with sdpa_kernel(SDPBackend.MATH):
        for domain,ids in evaluation[split].items():
            total=0.;count=0
            for at in range(0,len(ids),2):
                x,y=batch_tensor(arrays[split],ids[at:at+2],bp['pad_id'],device)
                _,loss,_=model(x,y);n=int((y!=-100).sum());total+=float(loss)*n;count+=n
            if not count:raise ValueError('Empty held-out evaluation')
            result[domain]={'nll':total/count,'perplexity':math.exp(min(50,total/count)),'targets':count,'windows':len(ids)}
    result['weighted_nll']=sum(result[d]['nll']*weight for d,weight in bp['mixture'].items())
    return result


class Resources:
    def __init__(self,root,device):
        self.root=root;self.device=device;self.stop=threading.Event();self.started=time.monotonic()
        self.path=root/('resources-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.jsonl')
        self.cpu=time.process_time();self.samples=[];self.energy=0.;self.last=None
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def start(self):self.thread.start()
    def loop(self):
        while not self.stop.is_set():
            row={'elapsed_seconds':time.monotonic()-self.started,'cpu_seconds':time.process_time()-self.cpu,
                 'peak_process_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}
            if self.device=='cuda':
                try:
                    executable='/usr/lib/wsl/lib/nvidia-smi' if Path('/usr/lib/wsl/lib/nvidia-smi').exists() else 'nvidia-smi'
                    value=subprocess.check_output([executable,'--query-gpu=power.draw,utilization.gpu,memory.used,temperature.gpu','--format=csv,noheader,nounits'],text=True,timeout=5).splitlines()[0]
                    watts,util,mem,temp=map(float,value.split(','));row.update(gpu_watts=watts,gpu_utilization_percent=util,gpu_memory_mib=mem,gpu_temperature_c=temp)
                    if self.last:self.energy+=(row['elapsed_seconds']-self.last['elapsed_seconds'])*(watts+self.last['gpu_watts'])/2
                    self.last=row
                except (OSError,ValueError,subprocess.SubprocessError):row['gpu_measurement']='unavailable'
            self.samples.append(row)
            with self.path.open('a') as f:f.write(json.dumps(row)+'\n')
            self.stop.wait(5)
    def finish(self):
        self.stop.set();self.thread.join(timeout=8)
        return {'wall_seconds':time.monotonic()-self.started,'cpu_seconds':time.process_time()-self.cpu,
                'peak_process_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                'peak_torch_allocated_bytes':torch.cuda.max_memory_allocated() if self.device=='cuda' else None,
                'sampled_device_energy_kwh':self.energy/3_600_000 if self.last else None,
                'gpu_utilization_mean_percent':float(np.mean([s['gpu_utilization_percent'] for s in self.samples if 'gpu_utilization_percent' in s])) if self.last else None,
                'samples_file':self.path.name,'samples_sha256':file_hash(self.path),
                'scope':'Measured loop wall/CPU and process peak RSS; device-wide sampled power/utilization. Training includes periodic validation/checkpoint overhead; initial loading/baseline and final test evaluation excluded. Not whole-system electricity.'}


def create(root,dataset,args):
    if root.exists():raise ValueError('Run root already exists; resume or choose a fresh branch')
    bp,_=load(dataset);root.mkdir(parents=True)
    runtime=configure(args.device,args.precision,args.attention,args.threads)
    config=Config(vocab_size=bp['vocab_size'],context=bp['context'],width=args.width,heads=args.heads,kv_heads=args.kv_heads,layers=args.layers,hidden=args.hidden)
    torch.manual_seed(args.seed);model=LanguageModel(config)
    parent=None;parent_opt=None
    if getattr(args,'parent',None):
        if not args.expected_parent_key or args.parent_step is None:raise ValueError('A branch requires the parent signer and checkpoint step')
        validate_lineage(args.parent,args.expected_parent_key)
        previous=json.loads((args.parent/'recipe.json').read_bytes())
        if previous['blueprint_sha256']!=file_hash(dataset/'blueprint.json'):raise ValueError('This branch command requires the same token Blueprint')
        model,parent_opt,desc=load_checkpoint(args.parent,args.parent_step,'cpu',True);config=model.config
        parent={'public_key':args.expected_parent_key,'checkpoint_step':args.parent_step,'state_sha256':desc['state_sha256'],
                'descriptor_sha256':file_hash(args.parent/'checkpoints'/f'{args.parent_step:07d}'/'checkpoint.json'),
                'schedule':'New phase starts at step zero with its own frozen batches and learning-rate schedule; AdamW moments inherited.'}
    stats=schedule(root,dataset,args.batch_size,args.steps,args.seed)
    recipe={'schema':'hypertrix.lab-recipe/1','blueprint_sha256':file_hash(dataset/'blueprint.json'),
        'schedule_sha256':stats['sha256'],'seed':args.seed,'steps':args.steps,'batch_size':args.batch_size,'micro_batch':args.micro_batch,
        'learning_rate':args.learning_rate,'pad_id':bp['pad_id'],'runtime':runtime,'checkpoint_every':args.checkpoint_every,
        'compiler':'torch-inductor-default' if args.compile else 'eager',
        'initialization':'Random normal .02; residual projections scaled by 1/sqrt(2*layers); tied embeddings; no pretrained weights.',
        'optimizer':'AdamW betas (.9,.95), decay .1, clip norm 1, 5% warmup, cosine to 10%, FP32 master/optimizer state.',
        'selection':'Minimum mixture-weighted held-out validation NLL; test once at selected checkpoint.',
        'scope':'Single-device lab lineage. Multi-node DDP requires a separately recorded run; not an Oracle training certificate.'}
    if parent:
        recipe['parent']=parent;recipe['initialization']='Continued weights and AdamW state from the pinned public parent checkpoint.'
    write_json(root/'config.json',asdict(config));write_json(root/'recipe.json',recipe)
    pins=implementation();write_json(root/'implementation.json',pins)
    for name in pins:immutable_write(root/'implementation'/name,(Path(__file__).parents[1]/name).read_bytes())
    keygen(root/'operator.private.pem',root/'operator.public.key')
    attest(root,'initialization',{'config_sha256':file_hash(root/'config.json'),'recipe_sha256':file_hash(root/'recipe.json'),
        'implementation_sha256':file_hash(root/'implementation.json'),'schedule_manifest_sha256':file_hash(root/'schedule.json'),
        'blueprint_sha256':recipe['blueprint_sha256'],'parent':parent})
    save_checkpoint(root,model,parent_opt or optimizer_for(model,recipe),0)
    return recipe


def train(root,dataset,device,resume=False,stop_step=None):
    recipe=json.loads((root/'recipe.json').read_bytes());runtime=configure(device,recipe['runtime']['precision'],recipe['runtime']['attention'],recipe['runtime']['threads'])
    if runtime!=recipe['runtime']:raise ValueError('Environment differs; create a new measured cohort/branch')
    check_implementation(root);bp,arrays=load(dataset)
    if file_hash(dataset/'blueprint.json')!=recipe['blueprint_sha256']:raise ValueError('Blueprint differs')
    work=np.load(root/'schedule.npy',mmap_mode='r');evaluation=json.loads((dataset/'evaluation.json').read_bytes())
    # A crash between rename and attestation leaves an unaccepted artifact;
    # recovery never treats that artifact as a signed checkpoint.
    available=sorted(r['payload']['step'] for r in validate_lineage(root) if r['kind']=='checkpoint')
    if not resume and available!=[0]:raise ValueError('Run already started; use resume')
    start=available[-1] if resume else 0;model,opt,_=load_checkpoint(root,start,device,True)
    compute=torch.compile(model) if recipe.get('compiler')=='torch-inductor-default' else model
    params=sum(p.numel() for p in model.parameters());stop=[False]
    signal.signal(signal.SIGTERM,lambda *_:stop.__setitem__(0,True));signal.signal(signal.SIGINT,lambda *_:stop.__setitem__(0,True))
    best=float('inf');selected=0
    for step in available:
        desc=json.loads((root/'checkpoints'/f'{step:07d}'/'checkpoint.json').read_bytes())
        if desc['evaluation'] and desc['evaluation']['weighted_nll']<best:best=desc['evaluation']['weighted_nll'];selected=step
    if start==0 and not (root/'baseline.json').exists():write_json(root/'baseline.json',evaluate(model,arrays,bp,evaluation,device))
    meter=Resources(root,device);meter.start();tokens=0;began=time.monotonic();last=start;step=start
    final=min(recipe['steps'],stop_step or recipe['steps'])
    event(root,'training-started',start_step=start,planned_steps=recipe['steps'],parameters=params)
    try:
        for at in range(start,final):
            if stop[0]:break
            result=update(compute,opt,arrays['train'],work[at],recipe,at,device);step=at+1;tokens+=result['target_tokens']
            if step%10==0:
                elapsed=time.monotonic()-began
                event(root,'progress',step=step,**result,target_tokens_this_session=tokens,elapsed_seconds=elapsed,targets_per_second=tokens/elapsed)
                progress(root,'Training weights',job=f'Seed III · {params/1e6:.1f}M',role='model-trainer',status='running',completed=step,total=recipe['steps'],unit='steps',
                    step=step,loss=result['loss'],target_tokens=tokens,targets_per_second=tokens/elapsed,elapsed_seconds=elapsed,parameters=params,
                    checkpoint_step=last,selected_step=selected,validation_nll=best if math.isfinite(best) else None)
            if step%recipe['checkpoint_every']==0 or step==final:
                score=evaluate(model,arrays,bp,evaluation,device)
                save_checkpoint(root,model,opt,step,score);last=step
                if score['weighted_nll']<best:best=score['weighted_nll'];selected=step
                event(root,'checkpoint',step=step,validation=score)
        if step>last:
            score=evaluate(model,arrays,bp,evaluation,device);save_checkpoint(root,model,opt,step,score)
            if score['weighted_nll']<best:best=score['weighted_nll'];selected=step
    finally:
        resources=meter.finish()
    complete=step==recipe['steps'];test=None
    if complete:
        del compute,model,opt
        if device=='cuda':torch.cuda.empty_cache()
        selected_model,_,_=load_checkpoint(root,selected,device)
        test=evaluate(selected_model,arrays,bp,evaluation,device,'test')
    summary={'schema':'hypertrix.lab-result/1','status':'complete' if complete else 'paused','start_step':start,'completed_steps':step,
        'planned_steps':recipe['steps'],'selected_step':selected,'validation_weighted_nll':best if math.isfinite(best) else None,
        'parameters':params,'selected_test':test,'target_tokens_this_session':tokens,'resources':resources,
        'estimated_training_flops':6*params*tokens,'flops_method':'6*N*nonpadding targets, excluding attention quadratic term, evaluation and replay.',
        'blueprint_sha256':recipe['blueprint_sha256']}
    name=f'summary-{step:07d}.json';write_json(root/name,summary);attest(root,'training-result',{'path':name,'sha256':file_hash(root/name)})
    pending=root/'summary.pending';pending.write_bytes(canonical_json(summary));pending.replace(root/'summary.json')
    progress(root,'Training complete' if complete else 'Training paused',job=f'Seed III · {params/1e6:.1f}M',role='model-trainer',status=summary['status'],
             completed=step,total=recipe['steps'],unit='steps',selected_step=selected,validation_nll=summary['validation_weighted_nll'],parameters=params)
    event(root,'training-finished',**summary);return summary


def replay(root,dataset,start,end,device='cuda'):
    recipe=json.loads((root/'recipe.json').read_bytes());check_implementation(root)
    if configure(device,recipe['runtime']['precision'],recipe['runtime']['attention'],recipe['runtime']['threads'])!=recipe['runtime']:raise ValueError('Exact replay requires the recorded environment')
    bp,arrays=load(dataset)
    if file_hash(dataset/'blueprint.json')!=recipe['blueprint_sha256']:raise ValueError('Blueprint differs')
    if not 0<=start<end<=recipe['steps']:raise ValueError('Invalid replay interval')
    model,opt,_=load_checkpoint(root,start,device,True);expected,expected_opt,_=load_checkpoint(root,end,'cpu',True)
    compute=torch.compile(model) if recipe.get('compiler')=='torch-inductor-default' else model
    work=np.load(root/'schedule.npy',mmap_mode='r');began=time.monotonic();meter=Resources(root,device);meter.start()
    try:
        for step in range(start,end):
            update(compute,opt,arrays['train'],work[step],recipe,step,device)
            if step%10==0:progress(root,'Replaying training',job='Seed III verifier',role='model-replay',status='running',completed=step-start+1,total=end-start,unit='steps')
    finally:resources=meter.finish()
    differences=[{'name':k,'exact':torch.equal(v.cpu(),expected.state_dict()[k]),'max_absolute':float((v.cpu()-expected.state_dict()[k]).abs().max())} for k,v in model.state_dict().items()]
    optimizer_exact=True
    for (_,a),(_,b) in zip(model.named_parameters(),expected.named_parameters()):
        sa=opt.state.get(a,{});sb=expected_opt.state.get(b,{})
        if sa.keys()!=sb.keys() or any(not torch.equal(sa[k].cpu(),sb[k]) for k in sa):optimizer_exact=False
    result={'schema':'hypertrix.lab-replay/1','start_step':start,'end_step':end,'accepted':all(d['exact'] for d in differences) and optimizer_exact,
        'tensor_count':len(differences),'exact_tensors':sum(d['exact'] for d in differences),'optimizer_exact':optimizer_exact,
        'checks':differences,'resources':resources,'elapsed_seconds':time.monotonic()-began,'scope':'Fresh process; same GPU family/runtime; stated update interval only.'}
    path=root/f'replay-{start:07d}-{end:07d}.json';write_json(path,result);attest(root,'replay',{'path':path.name,'sha256':file_hash(path),'accepted':result['accepted']})
    progress(root,'Replay verified' if result['accepted'] else 'Replay mismatch',job='Seed III verifier',role='model-replay',status='complete' if result['accepted'] else 'failed',completed=end-start,total=end-start,unit='steps')
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','create','fork','train','replay']);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--dataset',type=Path);p.add_argument('--corpus',type=Path);p.add_argument('--device',choices=['cpu','cuda'],default='cuda')
    p.add_argument('--precision',default='bf16');p.add_argument('--attention',default='flash');p.add_argument('--threads',type=int,default=4)
    p.add_argument('--width',type=int,default=768);p.add_argument('--heads',type=int,default=12);p.add_argument('--kv-heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=18);p.add_argument('--hidden',type=int,default=2048)
    p.add_argument('--seed',type=int,default=20261015);p.add_argument('--steps',type=int,default=16000);p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--micro-batch',type=int,default=16);p.add_argument('--learning-rate',type=float,default=.0006);p.add_argument('--checkpoint-every',type=int,default=1000)
    p.add_argument('--compile',action='store_true')
    p.add_argument('--parent',type=Path);p.add_argument('--parent-step',type=int);p.add_argument('--expected-parent-key')
    p.add_argument('--resume',action='store_true');p.add_argument('--stop-step',type=int);p.add_argument('--start',type=int);p.add_argument('--end',type=int)
    a=p.parse_args()
    if a.command=='prepare':result=prepare(a.root,a.corpus)
    elif a.command in ('create','fork'):
        if a.command=='fork' and not a.parent:p.error('fork requires --parent')
        result=create(a.root,a.dataset,a)
    elif a.command=='train':result=train(a.root,a.dataset,a.device,a.resume,a.stop_step)
    else:
        result=replay(a.root,a.dataset,a.start,a.end,a.device)
        if not result['accepted']:raise SystemExit(json.dumps(result))
    print(json.dumps(result))


if __name__=='__main__':main()
