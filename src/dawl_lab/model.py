"""Dense causal decoder with selectable grouped-query attention and a KV cache."""
from dataclasses import dataclass
import math
import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class Config:
    vocab_size:int=16384
    context:int=1024
    width:int=768
    heads:int=12
    kv_heads:int=4
    layers:int=18
    hidden:int=2048
    rope_base:float=10000.0

    def __post_init__(self):
        if min(self.vocab_size,self.context,self.width,self.heads,self.kv_heads,self.layers,self.hidden)<1:raise ValueError('Nonpositive configuration')
        if self.width%self.heads or self.heads%self.kv_heads or self.width//self.heads%2:raise ValueError('Invalid attention dimensions')


class Attention(nn.Module):
    def __init__(self,c):
        super().__init__();self.c=c;d=c.width//c.heads
        self.q=nn.Linear(c.width,c.width,bias=False)
        self.k=nn.Linear(c.width,c.kv_heads*d,bias=False);self.v=nn.Linear(c.width,c.kv_heads*d,bias=False)
        self.out=nn.Linear(c.width,c.width,bias=False)
        frequency=1/(c.rope_base**(torch.arange(0,d,2).float()/d))
        angle=torch.outer(torch.arange(c.context),frequency)
        self.register_buffer('cosine',angle.cos(),persistent=False);self.register_buffer('sine',angle.sin(),persistent=False)

    def rotate(self,x,start):
        c=self.cosine[start:start+x.shape[-2]].to(x.dtype);s=self.sine[start:start+x.shape[-2]].to(x.dtype)
        a,b=x[...,0::2],x[...,1::2]
        return torch.stack((a*c-b*s,a*s+b*c),dim=-1).flatten(-2)

    def forward(self,x,cache=None,use_cache=False):
        b,t,w=x.shape;c=self.c;d=w//c.heads;start=cache[0].shape[-2] if cache is not None else 0
        q=self.rotate(self.q(x).view(b,t,c.heads,d).transpose(1,2),start)
        k=self.rotate(self.k(x).view(b,t,c.kv_heads,d).transpose(1,2),start)
        v=self.v(x).view(b,t,c.kv_heads,d).transpose(1,2)
        if cache is not None:k=torch.cat((cache[0],k),dim=-2);v=torch.cat((cache[1],v),dim=-2)
        # Single-token cached decoding sees the entire prefix; full training is causal.
        mask=None
        if start and t>1:
            mask=torch.arange(k.shape[-2],device=x.device)[None,:] <= torch.arange(start,start+t,device=x.device)[:,None]
        z=F.scaled_dot_product_attention(q,k,v,attn_mask=mask,is_causal=start==0,dropout_p=0,enable_gqa=c.heads!=c.kv_heads)
        return self.out(z.transpose(1,2).reshape(b,t,w)), (k,v) if use_cache else None


class FeedForward(nn.Module):
    def __init__(self,c):
        super().__init__();self.up=nn.Linear(c.width,c.hidden,bias=False);self.gate=nn.Linear(c.width,c.hidden,bias=False);self.down=nn.Linear(c.hidden,c.width,bias=False)
    def forward(self,x):return self.down(F.silu(self.gate(x))*self.up(x))


class Block(nn.Module):
    def __init__(self,c):
        super().__init__();self.attention_norm=nn.RMSNorm(c.width,eps=1e-6);self.attention=Attention(c)
        self.ff_norm=nn.RMSNorm(c.width,eps=1e-6);self.ff=FeedForward(c)
    def forward(self,x,cache=None,use_cache=False):
        z,cache=self.attention(self.attention_norm(x),cache,use_cache);x=x+z
        return x+self.ff(self.ff_norm(x)),cache


class LanguageModel(nn.Module):
    def __init__(self,config):
        super().__init__();self.config=config;self.embedding=nn.Embedding(config.vocab_size,config.width)
        self.blocks=nn.ModuleList(Block(config) for _ in range(config.layers));self.norm=nn.RMSNorm(config.width,eps=1e-6)
        for module in self.modules():
            if isinstance(module,(nn.Linear,nn.Embedding)):nn.init.normal_(module.weight,std=.02)
        for block in self.blocks:
            nn.init.normal_(block.attention.out.weight,std=.02/math.sqrt(2*config.layers))
            nn.init.normal_(block.ff.down.weight,std=.02/math.sqrt(2*config.layers))

    def forward(self,ids,labels=None,cache=None,use_cache=False,only_last=False):
        before=cache[0][0].shape[-2] if cache else 0
        if ids.ndim!=2 or ids.shape[1]+before>self.config.context:raise ValueError('Context exceeds trained configuration')
        x=self.embedding(ids);new_cache=[]
        for i,block in enumerate(self.blocks):
            x,part=block(x,cache[i] if cache else None,use_cache);new_cache.append(part)
        x=self.norm(x);x=x[:,-1:] if only_last else x
        logits=F.linear(x,self.embedding.weight)
        loss=None if labels is None else F.cross_entropy(logits.float().reshape(-1,logits.shape[-1]),labels.reshape(-1),ignore_index=-100)
        return logits,loss,new_cache if use_cache else None
