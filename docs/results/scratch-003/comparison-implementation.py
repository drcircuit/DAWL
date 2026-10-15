"""Paired sampled evaluation and fixed continuations after checkpoint selection."""
import argparse
import gc
import json
from pathlib import Path
import time
import torch
from tokenizers import Tokenizer
from dawl_node.common import sign,load_key,write_json
from dawl_scratch.run import load_checkpoint as load_first
from dawl_seed.run import configure,load_checkpoint,evaluate
from dawl_seed.data import load,file_hash
from dawl_seed.progress import progress

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--previous',type=Path,required=True);a=p.parse_args()
runtime=configure('cuda','fp32','math');dataset=a.root/'dataset';bp,arrays=load(dataset)
bp['_evaluation']=json.loads((dataset/'evaluation.json').read_bytes());index=json.loads((dataset/'windows.json').read_bytes())
tok=Tokenizer.from_file(str(dataset/'tokenizer.json'))
old_bp=json.loads((a.previous/'dataset/blueprint.json').read_bytes())
if old_bp['tokenizer_sha256']!=bp['tokenizer_sha256']:raise ValueError('Paired comparison requires the identical tokenizer')
models=[('seed1_dense',a.previous/'dense',load_first),('seed1_moe',a.previous/'moe',load_first),('seed2_dense',a.root/'dense',load_checkpoint)]
prompts=['The traveller looked up at the stars and','function fibonacci(n) {\n  if (n <= 1) return n;\n  ', 'Det var en stille kveld, og langs fjorden ']
result={'schema':'hypertrix.seed-paired-evaluation/1','blueprint_sha256':file_hash(dataset/'blueprint.json'),
        'evaluation_sha256':bp['evaluation_sha256'],'runtime':runtime,'implementation_sha256':file_hash(Path(__file__)),
        'generation':{'temperature':0,'max_new_tokens':64,'context':512,'pad_and_bos_suppressed':True},'models':{},
        'scope':'Identical frozen tokenizer and sampled windows; FP32 math evaluation on one GPU. Models differ in architecture, corpus and training exposure; effects are not isolated.'}
for i,(name,root,loader) in enumerate(models):
    progress(a.root/'comparison','Comparing checkpoints',job='Checkpoint comparison',role='model-replay',status='running',completed=i,total=3,unit='models')
    summary=json.loads((root/'summary.json').read_bytes());step=summary['selected_step'];model,_,desc=loader(root,step,'cuda');model.eval()
    row={'step':step,'parameters':sum(p.numel() for p in model.parameters()),'checkpoint_sha256':desc['state_sha256'],
         'operator_public_key':(root/'operator.public.key').read_text().strip(),'validation':evaluate(model,arrays,bp,'cuda',index),
         'test':evaluate(model,arrays,bp,'cuda',index,'test'),'continuations':[]}
    with torch.inference_mode():
        for prompt in prompts:
            ids=tok.encode(prompt,add_special_tokens=False).ids;tokens=torch.tensor([ids],device='cuda');generated=[];began=time.monotonic()
            for _ in range(64):
                logits=model(tokens[:,-512:])[0][0,-1].float();logits[bp['pad_id']]=float('-inf');logits[0]=float('-inf')
                token=int(logits.argmax())
                if token==bp['eos_id']:break
                generated.append(token);tokens=torch.cat([tokens,torch.tensor([[token]],device='cuda')],dim=1)
            row['continuations'].append({'prompt':prompt,'text':tok.decode(generated,skip_special_tokens=True),'tokens':generated,'elapsed_seconds':time.monotonic()-began})
    result['models'][name]=row;del model;gc.collect();torch.cuda.empty_cache()
write_json(a.root/'comparison.json',result)
receipt=sign({'schema':'hypertrix.seed-comparison-receipt/1','comparison_sha256':file_hash(a.root/'comparison.json'),
              'blueprint_sha256':result['blueprint_sha256']},load_key(a.root/'dense/operator.private.pem'))
write_json(a.root/'comparison-receipt.json',receipt)
progress(a.root/'comparison','Comparison complete',job='Checkpoint comparison',role='model-replay',status='complete',completed=3,total=3,unit='models')
print(json.dumps({name:row['test'] for name,row in result['models'].items()}),flush=True)
