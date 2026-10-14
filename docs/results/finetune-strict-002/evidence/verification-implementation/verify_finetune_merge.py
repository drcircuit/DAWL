"""Independently recompute the selected LoRA merge on CPU, tensor by tensor."""
import argparse
import math
from pathlib import Path
import sys
import torch
from safetensors.torch import load_file
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_node.common import read_json,write_json
from dawl_finetune.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('/pilot'));a=p.parse_args();root=a.root
torch.set_num_threads(4)
selected=read_json(root/'training-summary.json')['selected_evaluation']['selected']
recipe=read_json(root/'recipe.json');config=read_json(root/'base/config.json')
base=load_file(str(root/'base/model.safetensors'))
adapter=load_file(str(root/'checkpoints'/selected/'adapter_model.safetensors'))
merged=load_file(str(root/'exports'/selected/'model.safetensors'))
checks=[];used=set();adapted=0
for name,actual in merged.items():
    original=base[name].to(torch.bfloat16)
    prefix='base_model.model.'+name.removesuffix('.weight')
    ak=prefix+'.lora_A.weight';bk=prefix+'.lora_B.weight'
    if ak in adapter:
        # PEFT 0.21.2 Linear.merge(safe_merge=True) explicitly casts the delta
        # to orig_dtype BEFORE its in-place addition to the base. Preserve both
        # rounding points; one final cast alone is a different computation.
        delta=(adapter[bk].float() @ adapter[ak].float())*(recipe['alpha']/recipe['rank'])
        expected=original+delta.to(torch.bfloat16);used.update((ak,bk));adapted+=1
    else:expected=original
    exact=torch.equal(actual,expected)
    checks.append({'tensor':name,'exact_match':exact,'max_abs_difference':float((actual.float()-expected.float()).abs().max())})
missing=set(base)-set(merged)
if missing:
    if missing!={'lm_head.weight'} or not config.get('tie_word_embeddings') or not torch.equal(base['lm_head.weight'],base['model.embed_tokens.weight']):
        raise ValueError('Unexplained omitted base tensors')
if used!=set(adapter):raise ValueError('Unexplained adapter tensors')
result={'schema':'hypertrix.local-merge-verification/1','selected':selected,'passed':all(c['exact_match'] for c in checks),
        'checked_tensors':len(checks),'adapted_linear_maps':adapted,'adapter_tensors_consumed':len(used),
        'tied_output_omission_verified':bool(missing),'checks':checks,'verifier_script_sha256':file_hash(Path(__file__)),
        'arithmetic':'BF16(BF16(base) + BF16((alpha/r) * FP32(B) @ FP32(A)))',
        'scope':'Separate CPU process recomputed PEFT 0.21.2 safe-merge arithmetic with both BF16 rounding points and checked every other exported tensor against the frozen BF16 base. This checks the saved merge, not the optimization or GGUF runtime.'}
write_json(root/'merge-verification.json',result)
print({k:v for k,v in result.items() if k!='checks'})
if not result['passed']:raise SystemExit(1)
