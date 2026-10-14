"""Compare exact trainer initialization with the abbreviated RNG restoration."""
import hashlib,json,gc,math,time
from pathlib import Path
import sys
sys.path.insert(0,'/app/src')
import torch
import numpy as np
from peft import get_peft_model_state_dict
from safetensors.torch import load_file
from dawl_finetune.run import seed,tokenizer,base_model,evaluate,probes,chat_ids,add_adapter,CONFIG
from dawl_finetune.data import load,file_hash
from dawl_node.common import write_json,read_json

root=Path('/pilot');started=time.time();deadline=read_json(root/'gpu-session.json')['deadline_unix']-30
def sha(t):return hashlib.sha256(t.cpu().numpy().tobytes()).hexdigest()
def snapshot(model):
    cpu_rng=sha(torch.get_rng_state());cuda_rng=sha(torch.cuda.get_rng_state())
    model=add_adapter(model);model.config.use_cache=False
    tensors={k:t.detach().cpu().clone() for k,t in get_peft_model_state_dict(model).items()}
    reference=load_file(str(root/'checkpoints/epoch-1/adapter_model.safetensors'))
    distance=math.sqrt(sum(float((reference[k]-t).float().square().sum()) for k,t in tensors.items()))
    return model,{'cpu_rng':cpu_rng,'cuda_rng':cuda_rng,'adapter_hashes':{k:sha(t) for k,t in tensors.items()},
                  'epoch_1_distance_from_initial':distance}

seed();data=load(root);tok=tokenizer(root);model=base_model(root)
evaluate(model,data,'validation');evaluate(model,data,'test');original_probes=probes(model,tok)
model,full=snapshot(model)
write_json(root/'replay-init-full.json',full);print({'phase':'full-trainer-initialization','distance':full['epoch_1_distance_from_initial']},flush=True)
del model;gc.collect();torch.cuda.empty_cache()
if time.time()>deadline-180:raise RuntimeError('Remaining budget insufficient for abbreviated comparison')
seed();model=base_model(root)
with torch.inference_mode():
    model.eval();torch.manual_seed(61014)
    ids=chat_ids(tok,'What should the Bank of England do during a banking panic according to Bagehot?')
    model.generate(ids,attention_mask=torch.ones_like(ids),max_new_tokens=220,do_sample=True,
                   temperature=.7,top_p=.8,top_k=20,pad_token_id=tok.eos_token_id)
model,short=snapshot(model)
write_json(root/'replay-init-abbreviated.json',short)
differences=[k for k in full['adapter_hashes'] if full['adapter_hashes'][k]!=short['adapter_hashes'][k]]
result={'schema':'hypertrix.replay-initialization-diagnostic/1','full_vs_abbreviated_identical':not differences,
        'cpu_rng_equal':full['cpu_rng']==short['cpu_rng'],'cuda_rng_equal':full['cuda_rng']==short['cuda_rng'],
        'differing_adapter_tensors':differences,'full_distance':full['epoch_1_distance_from_initial'],
        'abbreviated_distance':short['epoch_1_distance_from_initial'],
        'recorded_distance':read_json(root/'training-summary.json')['epochs'][0]['adapter_delta_l2_from_initial'],
        'baseline_generations_match':original_probes['generations']==read_json(root/'baseline.json')['probes']['generations'],
        'wall_seconds':time.time()-started,'script_sha256':file_hash(Path(__file__))}
write_json(root/'replay-initialization-diagnostic.json',result);print(result,flush=True)
