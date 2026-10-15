"""Pinned Llama implementation with fixed-A, trainable-B low-rank adapters.

No upstream Python, pickle checkpoints, automatic tokenizer, or model downloads.
"""
import hashlib
import math
from pathlib import Path
import numpy as np
import torch
from torch import nn
from safetensors.torch import load_file
from transformers import LlamaConfig, LlamaForCausalLM
from dawl_node.common import parse_json
from .data import batch


class FixedAdapter(nn.Module):
    def __init__(self,base,generator,rank=8,scale=2):
        super().__init__(); self.base=base; self.scale=scale
        self.register_buffer('A',torch.randn(rank,base.in_features,generator=generator)/math.sqrt(base.in_features))
        self.B=nn.Parameter(torch.zeros(base.out_features,rank))
    def forward(self,x): return self.base(x)+self.scale*((x@self.A.T)@self.B.T)


def setup():
    torch.set_num_threads(2); torch.manual_seed(20261014)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False


def assemble(objects,descriptor,path):
    digest=hashlib.sha256(); size=0; path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    # A verified local materialization is reusable on restart.
    if path.exists():
        with path.open('rb') as stream: existing=hashlib.file_digest(stream,'sha256').hexdigest()
        if existing==descriptor['sha256'] and path.stat().st_size==descriptor['bytes']: return path
        raise ValueError('local model materialization differs')
    with path.open('xb') as stream:
        for part in descriptor['chunks']:
            data=objects.read(part); stream.write(data); digest.update(data); size+=len(data)
    if digest.hexdigest()!=descriptor['sha256'] or size!=descriptor['bytes']:
        raise ValueError('model checkpoint differs from pinned upstream file')
    return path


def load_model(config_payload,checkpoint):
    setup(); config=LlamaConfig.from_dict(parse_json(config_payload))
    config._attn_implementation='eager'; config.use_cache=False
    if config.model_type!='llama' or config.hidden_size!=576 or config.num_hidden_layers!=30:
        raise ValueError('this training adapter supports pinned SmolLM2-135M')
    model=LlamaForCausalLM(config)
    state=load_file(str(checkpoint),device='cpu')
    missing,unexpected=model.load_state_dict(state,strict=False)
    if unexpected or missing!=['lm_head.weight'] or not config.tie_word_embeddings:
        raise ValueError(f'incomplete executable checkpoint: {missing}, {unexpected}')
    model.tie_weights()
    if model.lm_head.weight.data_ptr()!=model.model.embed_tokens.weight.data_ptr():
        raise ValueError('model output/input weight tying failed')
    for p in model.parameters(): p.requires_grad_(False)
    generator=torch.Generator().manual_seed(20261014)
    for name,module in list(model.named_modules()):
        if name.endswith(('.q_proj','.v_proj')):
            parent,attribute=name.rsplit('.',1)
            setattr(model.get_submodule(parent),attribute,FixedAdapter(module,generator))
    return model


def tensors(model,kind):
    return {name+'.'+kind:getattr(module,kind) for name,module in sorted(model.named_modules()) if isinstance(module,FixedAdapter)}


def export(model,kind='B'):
    layout=[]; chunks=[]; offset=0
    for name,tensor in tensors(model,kind).items():
        data=tensor.detach().cpu().float().contiguous().numpy().astype('<f4',copy=False).tobytes()
        layout.append({'name':name,'shape':list(tensor.shape),'offset':offset,'elements':tensor.numel()})
        chunks.append(data); offset+=len(data)
    return layout,b''.join(chunks)


def restore(model,payload,layout,kind='B'):
    actual=tensors(model,kind)
    if list(actual)!=[r['name'] for r in layout] or len(payload)!=sum(r['elements']*4 for r in layout):
        raise ValueError('adapter differs from executable model')
    with torch.no_grad():
        for r in layout:
            dest=actual[r['name']]
            if list(dest.shape)!=r['shape']: raise ValueError('adapter tensor shape differs')
            array=np.frombuffer(payload,dtype='<f4',count=r['elements'],offset=r['offset']).reshape(r['shape'])
            if not np.isfinite(array).all(): raise ValueError('nonfinite adapter parameter')
            dest.copy_(torch.from_numpy(array.copy()).to(dest.device))


def loss(model,records,objects,device):
    x,y=batch(records,objects,model.config.vocab_size)
    inputs=torch.from_numpy(x).to(device); targets=torch.from_numpy(y).to(device)
    logits=model(input_ids=inputs,use_cache=False).logits
    value=nn.functional.cross_entropy(logits.reshape(-1,logits.shape[-1]),targets.reshape(-1),ignore_index=-100)
    if not torch.isfinite(value): raise ValueError('nonfinite training loss')
    return value,int((targets!=-100).sum())


def train_local(model,records,objects,recipe,device):
    model.train(); parameters=[p for p in model.parameters() if p.requires_grad]
    optimizer=torch.optim.SGD(parameters,lr=recipe['learning_rate'],momentum=0,weight_decay=0)
    total=0.; tokens=0
    for start in range(0,len(records),recipe['batch_size']):
        optimizer.zero_grad(set_to_none=True)
        value,count=loss(model,records[start:start+recipe['batch_size']],objects,device)
        value.backward(); torch.nn.utils.clip_grad_norm_(parameters,recipe['clip_grad_norm'],error_if_nonfinite=True)
        optimizer.step(); total+=float(value.detach())*count; tokens+=count
    if device.startswith('cuda'): torch.cuda.synchronize()
    return total/tokens


def evaluate(model,records,objects,device):
    model.eval(); total=0.; tokens=0
    with torch.no_grad():
        for start in range(0,len(records),2):
            value,count=loss(model,records[start:start+2],objects,device)
            total+=float(value)*count; tokens+=count
    return {'loss':total/tokens,'perplexity':math.exp(total/tokens),'target_tokens':tokens,'sequences':len(records)}
