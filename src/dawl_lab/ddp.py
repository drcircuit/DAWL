"""Optional synchronous LAN training. Launch with torchrun, one process per GPU.

Each rank keeps its own identity. Only public parent artifacts are copied between
machines. DDP synchronizes gradients; it does not certify independent replay.
"""
import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import time
import numpy as np
import torch
import torch.distributed as dist
from torch.nn.parallel import DistributedDataParallel
from dawl_node.common import keygen,write_json,load_key,sign,verify_signature,immutable_write
from dawl_scratch.data import file_hash
from dawl_seed.progress import progress
from .model import Config,LanguageModel
from .data import load
from .run import configure,optimizer_for,update,load_checkpoint,save_checkpoint,attest,implementation,Resources,check_implementation,validate_lineage


def collective_test(root,rank,world):
    configure('cpu','fp32','math',1);torch.manual_seed(11)
    config=Config(vocab_size=260,context=16,width=32,heads=4,kv_heads=2,layers=2,hidden=64)
    model=LanguageModel(config);reference=LanguageModel(config);reference.load_state_dict(model.state_dict())
    wrapped=DistributedDataParallel(model)
    data=np.random.default_rng(7).integers(3,260,(4,17));data[1,5:]=2
    recipe={'learning_rate':.001,'steps':10,'pad_id':2,'micro_batch':1,'runtime':{'precision':'fp32','attention':'math'}}
    class Capture:
        param_groups=[{}]
        def __init__(self,m):self.m=m
        def zero_grad(self,**kw):self.m.zero_grad(**kw)
        def step(self):pass
    update(wrapped,Capture(wrapped),data,np.arange(4),recipe,0,'cpu',rank,world)
    # Compare the synchronized result with the same global batch in one process.
    update(reference,Capture(reference),data,np.arange(4),recipe,0,'cpu')
    differences=[]
    for a,b in zip(model.parameters(),reference.parameters()):
        torch.testing.assert_close(a.grad,b.grad,atol=2e-6,rtol=1e-4)
        differences.append(float((a.grad-b.grad).abs().max()))
    row={'rank':rank,'world_size':world,'accepted':True,'max_gradient_absolute_difference':max(differences)}
    gathered=[None]*world;dist.all_gather_object(gathered,row)
    if rank==0:
        root.mkdir(parents=True,exist_ok=False)
        write_json(root/'ddp-self-test.json',{'schema':'hypertrix.lab-ddp-test/1','ranks':gathered,
            'scope':'Real two-process CPU Gloo gradient exchange; single-host test, no multi-GPU or WAN performance claim.'})
        print(json.dumps(gathered),flush=True)


def train(parent,dataset,root,start,end,device,rank,world,expected_public,micro_batch=16):
    root.mkdir(parents=True,exist_ok=False)
    parent_recipe=json.loads((parent/'recipe.json').read_bytes())
    if file_hash(dataset/'blueprint.json')!=parent_recipe['blueprint_sha256']:raise ValueError('Parent dataset differs')
    if (parent/'operator.public.key').read_text().strip()!=expected_public:raise ValueError('Parent signer differs from job pin')
    validate_lineage(parent,expected_public)
    if not 0<=start<end<=parent_recipe['steps']:raise ValueError('Invalid update interval')
    if parent_recipe['batch_size']%world:raise ValueError('Global batch must divide across GPU ranks')
    if micro_batch<1:raise ValueError('Microbatch must be positive')
    runtime=configure(device,'bf16' if device=='cuda' else 'fp32','flash' if device=='cuda' else 'math')
    public=keygen(root/'operator.private.pem',root/'operator.public.key')
    identity={'rank':rank,'public_key':public,'runtime':runtime,'blueprint_sha256':parent_recipe['blueprint_sha256'],
              'parent_public_key':expected_public,'parent_checkpoint_sha256':file_hash(parent/'checkpoints'/f'{start:07d}'/'checkpoint.json'),
              'implementation':implementation(),'start':start,'end':end,'micro_batch':micro_batch}
    envelope=sign(identity,load_key(root/'operator.private.pem'));participants=[None]*world;dist.all_gather_object(participants,envelope)
    identities=[verify_signature(item,item['body']['public_key']) for item in participants]
    for item in identities:
        for field in ('blueprint_sha256','parent_public_key','parent_checkpoint_sha256','implementation','start','end'):
            if item[field]!=identity[field]:raise ValueError('Participants disagree about the job')
    if len({item['public_key'] for item in identities})!=world:raise ValueError('Duplicate participant key')
    write_json(root/'participants.json',participants)
    bp,arrays=load(dataset);work=np.load(parent/'schedule.npy',mmap_mode='r')
    if file_hash(parent/'schedule.npy')!=parent_recipe['schedule_sha256']:raise ValueError('Schedule differs')
    model,opt,parent_desc=load_checkpoint(parent,start,device,True)
    recipe={**parent_recipe,'runtime':runtime,'compiler':'eager','micro_batch':micro_batch,'world_size':world,'rank':rank,
        'parent':{'public_key':expected_public,'checkpoint':identity['parent_checkpoint_sha256'],'step':start},
        'participants_sha256':file_hash(root/'participants.json'),
        'scope':'Synchronous DDP full-model lab run. Rank measurements and checkpoint hashes; no independent replay or Oracle certification.'}
    write_json(root/'recipe.json',recipe);immutable_write(root/'config.json',(parent/'config.json').read_bytes())
    for name in ('schedule.npy','schedule.json'):immutable_write(root/name,(parent/name).read_bytes())
    pins=implementation();write_json(root/'implementation.json',pins)
    for name in pins:immutable_write(root/'implementation'/name,(Path(__file__).parents[1]/name).read_bytes())
    attest(root,'initialization',{'parent':recipe['parent'],'recipe_sha256':file_hash(root/'recipe.json'),
        'config_sha256':file_hash(root/'config.json'),'blueprint_sha256':recipe['blueprint_sha256'],
        'implementation_sha256':file_hash(root/'implementation.json'),'schedule_manifest_sha256':file_hash(root/'schedule.json'),
        'participants_sha256':recipe['participants_sha256']})
    wrapped=DistributedDataParallel(model,device_ids=[torch.cuda.current_device()] if device=='cuda' else None)
    meter=Resources(root,device);meter.start();began=time.monotonic();tokens=0;local_tokens=0
    try:
        for step in range(start,end):
            result=update(wrapped,opt,arrays['train'],work[step],recipe,step,device,rank,world);tokens+=result['target_tokens'];local_tokens+=result['local_target_tokens']
            if (step+1)%10==0:
                progress(root,'Distributed backpropagation',job=f'Seed III DDP rank {rank}',role='model-trainer',status='running',
                    completed=step+1,total=end,unit='steps',step=step+1,loss=result['loss'],world_size=world,
                    # Per-rank attributed work prevents summing the global tokens world times.
                    target_tokens=local_tokens,targets_per_second=local_tokens/(time.monotonic()-began),parameters=sum(p.numel() for p in model.parameters()))
        dist.barrier()
        if rank==0:save_checkpoint(root,model,opt,end)
    finally:resources=meter.finish()
    import hashlib
    digest=hashlib.sha256()
    for name,value in model.state_dict().items():
        digest.update(name.encode());digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    receipt=sign({'schema':'hypertrix.lab-ddp-rank-receipt/1','rank':rank,'world_size':world,'start':start,'end':end,
                  'final_model_digest':digest.hexdigest(),
                  'global_target_tokens':tokens,'local_target_tokens':local_tokens,'resources':resources,'participants_sha256':recipe['participants_sha256']},load_key(root/'operator.private.pem'))
    write_json(root/'rank-receipt.json',receipt)
    receipts=[None]*world;dist.all_gather_object(receipts,receipt)
    if rank==0:
        for item,who in zip(receipts,identities):verify_signature(item,who['public_key'])
        if len({item['body']['final_model_digest'] for item in receipts})!=1:raise ValueError('Ranks ended with different model tensors')
        write_json(root/'rank-receipts.json',receipts)
        attest(root,'ddp-completion',{'path':'rank-receipts.json','sha256':file_hash(root/'rank-receipts.json'),
              'participants_sha256':recipe['participants_sha256'],'end_step':end,'ranks':world})
    progress(root,'Distributed interval complete',job=f'Seed III DDP rank {rank}',role='model-trainer',status='complete',completed=end,total=end,unit='steps')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true');p.add_argument('--root',type=Path,required=True)
    p.add_argument('--parent',type=Path);p.add_argument('--dataset',type=Path);p.add_argument('--expected-parent-key');p.add_argument('--start',type=int,default=0);p.add_argument('--end',type=int,default=100)
    p.add_argument('--device',choices=['cpu','cuda'],default='cuda');p.add_argument('--micro-batch',type=int,default=16);a=p.parse_args()
    rank=int(os.environ['RANK']);world=int(os.environ['WORLD_SIZE']);local=int(os.environ.get('LOCAL_RANK',0))
    device='cpu' if a.self_test else a.device
    if device=='cuda':torch.cuda.set_device(local)
    dist.init_process_group('nccl' if device=='cuda' else 'gloo',timeout=timedelta(seconds=180))
    try:
        if a.self_test:collective_test(a.root,rank,world)
        else:train(a.parent,a.dataset,a.root/str(rank),a.start,a.end,device,rank,world,a.expected_parent_key,a.micro_batch)
    finally:dist.destroy_process_group()
