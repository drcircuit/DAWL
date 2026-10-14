"""Bounded local LoRA training with frozen evaluation and signed artifact lineage.

This is a single-operator experiment. Signatures attest artifact provenance, not
correct computation, independent verification, consensus, or factual truth.
"""
import argparse
from datetime import datetime, timezone
import gc
import importlib.metadata
import json
import math
from pathlib import Path
import random
import time
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import LoraConfig, get_peft_model, PeftModel
from dawl_node.common import canonical_json, keygen, load_key, read_json, sha256, sign, write_json
from .data import file_hash, load, prepare
from .questions import ECONOMICS, GENERAL
from .execution import configure, optimizer_options, recipe_with_policy, validate_recipe

CONFIG = {'schema':'hypertrix.local-lora-recipe/1','seed':20261014,'epochs':3,'batch_size':4,
          'accumulation':4,'sequence_length':512,'learning_rate':1e-4,'rank':16,'alpha':32,
          'dropout':0.0,'weight_decay':0.01,'clip_grad_norm':1.0,'warmup_fraction':0.03,
          'targets':['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
          'main_budget_seconds':6000,'total_gpu_budget_seconds':7100,'control_steps':160,
          'selection':'Lowest economics validation NLL with literature NLL <= 1.15 * baseline; otherwise retain baseline.',
          'precision':'BF16 parameters and activations; PEFT trainable adapters FP32; AdamW FP32',
          'attention':'sdpa','scope':'Local calibration branch; no Oracle quorum or distributed training claim.'}
BASE_CONFIG = dict(CONFIG)


def activate_recipe(recipe):
    validate_recipe(BASE_CONFIG,recipe)
    CONFIG.clear(); CONFIG.update(recipe)


def optimization_options():
    policy=CONFIG.get('execution_policy')
    return optimizer_options(policy['name']) if policy else {}


def clipping_options():
    return {'foreach':False} if CONFIG.get('execution_policy') else {}


def emit(root, event, **values):
    row = {'event':event,'utc':datetime.now(timezone.utc).isoformat(),**values}
    with (root/'events.jsonl').open('a') as f: f.write(json.dumps(row,allow_nan=False)+'\n')
    print(json.dumps(row,allow_nan=False),flush=True)


def attest(root, kind, payload):
    path = root/'lineage.jsonl'
    lines = path.read_text().splitlines() if path.exists() else []
    previous = json.loads(lines[-1])['id'] if lines else None
    record = sign({'schema':'hypertrix.local-experiment-record/1','sequence':len(lines),
                   'previous':previous,'kind':kind,'payload':payload},load_key(root/'operator.private.pem'))
    with path.open('a') as f: f.write(canonical_json(record).decode()+'\n'); f.flush()
    return record['id']


def tokenizer(root):
    return AutoTokenizer.from_pretrained(root/'base',local_files_only=True,trust_remote_code=False)


def base_model(root):
    return AutoModelForCausalLM.from_pretrained(root/'base',local_files_only=True,trust_remote_code=False,
                    dtype=torch.bfloat16,attn_implementation='sdpa').to('cuda')


def seed():
    settings=configure(CONFIG['execution_policy']['name']) if CONFIG.get('execution_policy') else None
    random.seed(CONFIG['seed']); np.random.seed(CONFIG['seed']); torch.manual_seed(CONFIG['seed'])
    torch.cuda.manual_seed_all(CONFIG['seed']); torch.set_num_threads(4)
    torch.backends.cuda.matmul.allow_tf32=False
    return settings


def add_adapter(model):
    return get_peft_model(model,LoraConfig(r=CONFIG['rank'],lora_alpha=CONFIG['alpha'],
                target_modules=CONFIG['targets'],lora_dropout=0,bias='none',task_type='CAUSAL_LM'))


@torch.inference_mode()
def evaluate(model, data, split):
    model.eval(); results={}
    for domain in ('economics','literature'):
        values = data[f'{split}_{domain}'][:64]
        losses=[]
        for start in range(0,len(values),2):
            ids=torch.tensor(values[start:start+2],device='cuda')
            loss=model(input_ids=ids,labels=ids,use_cache=False).loss.float().item()
            if not math.isfinite(loss): raise ValueError('Nonfinite evaluation loss')
            losses.append((loss,len(ids)*512))
        n=sum(count for _,count in losses); nll=sum(loss*count for loss,count in losses)/n
        results[domain]={'nll':nll,'perplexity':math.exp(nll),'tokens':n,'windows':len(values)}
    return results


def chat_ids(tok, prompt):
    encoded=tok.apply_chat_template([{'role':'user','content':prompt}],tokenize=True,
                    add_generation_prompt=True,enable_thinking=False,return_tensors='pt')
    return (encoded if isinstance(encoded,torch.Tensor) else encoded['input_ids']).to('cuda')


@torch.inference_mode()
def probes(model,tok):
    model.eval(); result={}
    letters=[tok.encode(c,add_special_tokens=False) for c in 'ABCD']
    if any(len(t)!=1 for t in letters): raise ValueError('Expected single-token answer labels')
    for domain,questions in [('economics',ECONOMICS),('general',GENERAL)]:
        rows=[]
        for index,(book,q,correct,distractors) in enumerate(questions):
            options=[correct]+list(distractors); random.Random(61014+index).shuffle(options)
            prompt=q+'\n'+'\n'.join(f'{chr(65+i)}. {s}' for i,s in enumerate(options))+'\nAnswer with only the letter of the correct option.'
            ids=chat_ids(tok,prompt)
            logits=model(input_ids=ids,use_cache=False).logits[0,-1].float()
            scores=logits[[t[0] for t in letters]].softmax(-1).cpu().tolist()
            guess=int(np.argmax(scores)); expected=options.index(correct)
            rows.append({'book_id':book,'question':q,'options':options,'expected':expected,'predicted':guess,
                         'conditional_option_probabilities':scores,'correct':guess==expected})
        result[domain]={'correct':sum(r['correct'] for r in rows),'total':len(rows),'items':rows}
    generated=[]
    for q in ['Explain how division of labour increases productivity according to Adam Smith.',
              'Explain Ricardo\'s theory of rent and its relationship to the price of corn.',
              'What should the Bank of England do during a banking panic according to Bagehot?']:
        torch.manual_seed(61014)
        ids=chat_ids(tok,q)
        output=model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=220,
                    do_sample=True,temperature=.7,top_p=.8,top_k=20,pad_token_id=tok.eos_token_id)
        generated.append({'question':q,'answer':tok.decode(output[0,len(ids[0]):],skip_special_tokens=True)})
    result['generations']=generated
    result['interpretation']='Small descriptive closed-book probes; neither broad capability benchmark nor proof of newly acquired book knowledge.'
    return result


def save_adapter(root, model, name, details):
    path=root/'checkpoints'/name
    path.mkdir(parents=True,exist_ok=False)
    model.save_pretrained(path,safe_serialization=True)
    files={p.name:{'sha256':file_hash(p),'bytes':p.stat().st_size} for p in path.iterdir() if p.is_file()}
    record=attest(root,'checkpoint',{'name':name,'base_lock_sha256':file_hash(root/'base-lock.json'),
                'dataset_lock_sha256':file_hash(root/'dataset-lock.json'),'recipe_sha256':file_hash(root/'recipe.json'),
                'files':files,**details})
    return {'name':name,'lineage_id':record,'files':files,**details}


def train(root, retry_failed_baseline=False):
    existing=(root/'gpu-session.json').exists()
    if existing and (not retry_failed_baseline or (root/'baseline.json').exists() or (root/'checkpoints').exists()):
        raise ValueError('Existing session; retry allowed only for a failed baseline before any weight updates')
    execution_settings=seed(); start=read_json(root/'gpu-session.json')['started_unix'] if existing else time.time()
    session={'started_unix':start,'deadline_unix':start+CONFIG['total_gpu_budget_seconds'],
             'gpu':torch.cuda.get_device_name(0),'packages':{n:importlib.metadata.version(n) for n in ('torch','transformers','peft','numpy')},
             'cuda':torch.version.cuda,'recipe_sha256':file_hash(root/'recipe.json')}
    if execution_settings is not None:session['execution_settings']=execution_settings
    if not existing:write_json(root/'gpu-session.json',session)
    attest(root,'baseline-retry' if existing else 'gpu-session',session)
    for name,desc in read_json(root/'base-lock.json')['files'].items():
        if file_hash(root/'base'/name)!=desc['sha256']: raise ValueError('Base changed: '+name)
    data=load(root); tok=tokenizer(root); model=base_model(root)
    baseline={'validation':evaluate(model,data,'validation'),'test':evaluate(model,data,'test'),'probes':probes(model,tok)}
    write_json(root/'baseline.json',baseline); attest(root,'baseline',{'sha256':file_hash(root/'baseline.json')})
    emit(root,'baseline',validation=baseline['validation'])
    model=add_adapter(model); model.config.use_cache=False
    trainable=[p for p in model.parameters() if p.requires_grad]
    initial=[p.detach().cpu().clone() for p in trainable]
    optimizer=torch.optim.AdamW(trainable,lr=CONFIG['learning_rate'],weight_decay=CONFIG['weight_decay'],**optimization_options())
    values=np.concatenate([data['train_economics'],data['train_literature']])
    batch=CONFIG['batch_size']; accum=CONFIG['accumulation']; updates_per_epoch=math.ceil(len(values)/(batch*accum))
    total_steps=updates_per_epoch*CONFIG['epochs']; step=0; consumed=0; epochs=[]; best=None
    best_loss=baseline['validation']['economics']['nll']; guard=baseline['validation']['literature']['nll']*1.15
    emit(root,'training-start',windows=len(values),tokens_per_epoch=len(values)*512,
         trainable_parameters=sum(p.numel() for p in trainable),total_parameters=sum(p.numel() for p in model.parameters()))
    for epoch in range(1,CONFIG['epochs']+1):
        order=np.random.default_rng(CONFIG['seed']+epoch).permutation(len(values))
        model.train(); epoch_losses=[]; epoch_windows=0; stop=False; epoch_start=time.time()
        for group in range(0,len(order),batch*accum):
            if time.time()-start > CONFIG['main_budget_seconds']-180:
                stop=True; break
            indices=order[group:group+batch*accum]; optimizer.zero_grad(set_to_none=True)
            progress=step/max(1,total_steps); warmup=CONFIG['warmup_fraction']
            scale=min(1.,(step+1)/max(1,total_steps*warmup)) if progress<warmup else .1+.9*.5*(1+math.cos(math.pi*(progress-warmup)/(1-warmup)))
            for g in optimizer.param_groups:g['lr']=CONFIG['learning_rate']*scale
            group_loss=0.
            for offset in range(0,len(indices),batch):
                subset=indices[offset:offset+batch]; ids=torch.tensor(values[subset],device='cuda')
                loss=model(input_ids=ids,labels=ids,use_cache=False).loss
                if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
                (loss*len(subset)/len(indices)).backward()
                group_loss+=loss.detach().float().item()*len(subset)/len(indices)
            norm=torch.nn.utils.clip_grad_norm_(trainable,CONFIG['clip_grad_norm'],error_if_nonfinite=True,**clipping_options())
            optimizer.step(); step+=1; consumed+=len(indices)*512; epoch_windows+=len(indices); epoch_losses.append(group_loss)
            if step%10==0 or step==1:
                emit(root,'training',epoch=epoch,step=step,epoch_fraction=epoch_windows/len(values),
                     loss=group_loss,gradient_norm=float(norm),tokens_seen=consumed,
                     elapsed_seconds=time.time()-start,peak_vram_bytes=torch.cuda.max_memory_allocated())
        validation=evaluate(model,data,'validation')
        details={'epoch':epoch,'complete_epoch':not stop,'optimizer_steps':step,'tokens_seen':consumed,
                 'epoch_windows':epoch_windows,'total_epoch_windows':len(values),'validation':validation,
                 'mean_update_loss':float(np.mean(epoch_losses)) if epoch_losses else None,'wall_seconds':time.time()-epoch_start}
        changed=sum(float((p.detach().cpu()-before).float().square().sum()) for p,before in zip(trainable,initial))
        details['adapter_delta_l2_from_initial']=math.sqrt(changed)
        saved=save_adapter(root,model,f'epoch-{epoch}',details); epochs.append(saved)
        if validation['economics']['nll']<best_loss and validation['literature']['nll']<=guard:
            best=saved; best_loss=validation['economics']['nll']
        emit(root,'checkpoint',**details,selected_so_far=best['name'] if best else 'baseline')
        if stop:break
    del optimizer,model,trainable,initial; gc.collect(); torch.cuda.empty_cache()
    model=base_model(root)
    if best:model=PeftModel.from_pretrained(model,root/'checkpoints'/best['name'],local_files_only=True)
    final={'selected':best['name'] if best else 'baseline','test':evaluate(model,data,'test'),'probes':probes(model,tok)}
    write_json(root/'selected-evaluation.json',final)
    summary={'baseline':baseline,'selected_evaluation':final,'epochs':epochs,'tokens_seen':consumed,
             'effective_epochs':consumed/(len(values)*512),'training_wall_seconds':time.time()-start,
             'peak_vram_bytes':torch.cuda.max_memory_allocated(),'scope':CONFIG['scope']}
    write_json(root/'training-summary.json',summary)
    attest(root,'training-result',{'summary_sha256':file_hash(root/'training-summary.json'),'selected_evaluation_sha256':file_hash(root/'selected-evaluation.json')})
    emit(root,'training-complete',selected=final['selected'],effective_epochs=summary['effective_epochs'],elapsed_seconds=time.time()-start)


def control(root):
    """Separate invented codebook branch, with held-out question wording."""
    if (root/'control-result.json').exists(): raise ValueError('Control already exists')
    seed(); deadline=read_json(root/'gpu-session.json')['deadline_unix']-30
    if time.time()>deadline-180: raise ValueError('Insufficient remaining GPU budget for control')
    tok=tokenizer(root); model=base_model(root)
    names=['Aster','Birch','Cobalt','Dune','Ember','Flint','Grove','Harbor','Iris','Jasper','Kestrel','Larch','Morrow','Nimbus','Opal','Pine']
    codes=['amber','violet','copper','silver','indigo','coral','ivory','teal','ochre','ruby','jade','pearl','azure','bronze','olive','scarlet']
    random.Random(20261014).shuffle(codes)
    facts=list(zip(names,codes)); write_json(root/'control-codebook.json',{'scope':'Invented lab routing codes, separate from economics model','facts':facts})
    @torch.inference_mode()
    def score(m):
        m.eval(); rows=[]
        for name,code in facts:
            prompt=f'In the fictional Hypertrix lab codebook, which routing color is assigned to station {name}? Reply with just the color.'
            ids=chat_ids(tok,prompt)
            out=m.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=12,do_sample=False,pad_token_id=tok.eos_token_id)
            answer=tok.decode(out[0,len(ids[0]):],skip_special_tokens=True).strip().lower().strip(' .\n')
            rows.append({'station':name,'expected':code,'answer':answer,'exact_match':answer==code})
        return {'correct':sum(r['exact_match'] for r in rows),'total':len(rows),'items':rows}
    before=score(model); model=add_adapter(model); model.config.use_cache=False
    examples=[]
    for name,code in facts:
        for prompt in [f'What is the routing color for {name} in the fictional Hypertrix lab?',
                       f'Give the fictional Hypertrix lab routing color of station {name}.',
                       f'In our invented Hypertrix codebook, {name} has which routing color?',
                       f'Hypertrix fictional lab lookup: station {name}; routing color?']:
            prefix=chat_ids(tok,prompt)[0].tolist()
            suffix=tok.encode(code+tok.eos_token,add_special_tokens=False)
            examples.append((prefix+suffix,[-100]*len(prefix)+suffix))
    opt=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=3e-4,**optimization_options())
    model.train(); rng=random.Random(1426); steps=0; losses=[]
    for step in range(CONFIG['control_steps']):
        if time.time()>deadline-90:break
        group=rng.sample(examples,8); length=max(len(ids) for ids,_ in group)
        ids=torch.tensor([x+[tok.pad_token_id]*(length-len(x)) for x,_ in group],device='cuda')
        labels=torch.tensor([y+[-100]*(length-len(y)) for _,y in group],device='cuda')
        mask=torch.tensor([[1]*len(x)+[0]*(length-len(x)) for x,_ in group],device='cuda')
        opt.zero_grad(set_to_none=True);loss=model(input_ids=ids,attention_mask=mask,labels=labels,use_cache=False).loss
        if not torch.isfinite(loss):raise ValueError('Nonfinite control loss')
        loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1,error_if_nonfinite=True,**clipping_options());opt.step();steps+=1;losses.append(float(loss.detach()))
        if steps%20==0:emit(root,'control-training',step=steps,loss=losses[-1])
    after=score(model)
    saved=save_adapter(root,model,'synthetic-control',{'steps':steps,'codebook_sha256':file_hash(root/'control-codebook.json'),
                        'parent':'baseline','separate_from_economics':True})
    result={'before':before,'after':after,'steps':steps,'examples':len(examples),'last_loss':losses[-1] if losses else None,
            'checkpoint':saved,'limitations':'Small memorization control on unseen question wording; not a test of reasoning or generalization to new facts.'}
    write_json(root/'control-result.json',result);attest(root,'control-result',{'sha256':file_hash(root/'control-result.json')})
    emit(root,'control-complete',before=before['correct'],after=after['correct'],total=len(facts),steps=steps)


def export(root):
    """Merge on CPU so export and chat do not consume the training GPU budget."""
    torch.set_num_threads(4)
    summary=read_json(root/'training-summary.json'); selected=summary['selected_evaluation']['selected']
    names=sorted({x['name'] for x in summary['epochs']} | ({'synthetic-control'} if (root/'control-result.json').exists() else set()))
    results=[]
    for name in names:
        destination=root/'exports'/name
        if destination.exists(): raise ValueError('Export exists: '+name)
        model=AutoModelForCausalLM.from_pretrained(root/'base',local_files_only=True,trust_remote_code=False,dtype=torch.bfloat16)
        model=PeftModel.from_pretrained(model,root/'checkpoints'/name,local_files_only=True)
        model=model.merge_and_unload(safe_merge=True)
        model.save_pretrained(destination,safe_serialization=True);tokenizer(root).save_pretrained(destination)
        files={p.name:{'sha256':file_hash(p),'bytes':p.stat().st_size} for p in destination.iterdir() if p.is_file()}
        record=attest(root,'merged-export',{'checkpoint':name,'files':files,'selected':name==selected,'precision':'BF16'})
        results.append({'name':name,'files':files,'lineage_id':record});emit(root,'export',name=name)
        del model;gc.collect()
    write_json(root/'exports.json',{'exports':results,'selected':selected})


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','train','control','export']);p.add_argument('--root',type=Path,default=Path('/pilot'))
    p.add_argument('--retry-failed-baseline',action='store_true')
    p.add_argument('--execution-policy',choices=['strict-auto','strict-math'],help='New preparation only; requires a new recipe and runtime image')
    p.add_argument('--epochs',type=int,choices=[1,2,3],help='New strict recipe only; explicit bounded full-corpus epochs')
    a=p.parse_args()
    if a.command!='prepare' and (a.execution_policy or a.epochs is not None):p.error('Execution policy and epochs are frozen during prepare')
    activate_recipe(recipe_with_policy(BASE_CONFIG,a.execution_policy,a.epochs) if a.command=='prepare' else read_json(a.root/'recipe.json'))
    if a.command=='prepare':
        keygen(a.root/'operator.private.pem',a.root/'operator.public.key')
        write_json(a.root/'recipe.json',CONFIG)
        write_json(a.root/'probes-lock.json',{'economics':ECONOMICS,'general':GENERAL,'training_use':False})
        result=prepare(a.root,tokenizer(a.root))
        attest(a.root,'experiment-inputs',{'dataset_lock_sha256':file_hash(a.root/'dataset-lock.json'),
               'recipe_sha256':file_hash(a.root/'recipe.json'),'probes_sha256':file_hash(a.root/'probes-lock.json')})
        emit(a.root,'prepared',**result)
    elif a.command=='train':train(a.root,a.retry_failed_baseline)
    else: globals()[a.command](a.root)


if __name__=='__main__':main()
