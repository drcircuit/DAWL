"""A compact causal Transformer, with optional top-two feed-forward experts."""
from dataclasses import asdict, dataclass
import math
import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class Config:
    vocab_size: int = 4096
    context: int = 512
    width: int = 256
    heads: int = 4
    layers: int = 6
    hidden: int = 1024
    experts: int = 1
    top_k: int = 2

    def __post_init__(self):
        if self.width % self.heads or self.width//self.heads % 2:
            raise ValueError('Attention head dimensions must be even')
        if not 1 <= self.experts <= 16 or (self.experts > 1 and not 1 <= self.top_k <= self.experts):
            raise ValueError('Invalid expert selection')


class FeedForward(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.up = nn.Linear(c.width, c.hidden, bias=False)
        self.down = nn.Linear(c.hidden, c.width, bias=False)

    def forward(self, x): return self.down(F.gelu(self.up(x)))


class Experts(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.router = nn.Linear(c.width, c.experts, bias=False)
        self.experts = nn.ModuleList(FeedForward(c) for _ in range(c.experts))
        self.top_k = c.top_k

    def forward(self, x, valid=None):
        shape = x.shape
        flat = x.reshape(-1, shape[-1])
        probability = self.router(flat).float().softmax(-1)
        # Stable sort gives a declared expert-ID tie-break for equal router scores.
        selected = torch.argsort(probability, dim=-1, descending=True, stable=True)[:, :self.top_k]
        weights = probability.gather(-1, selected)
        weights = weights / weights.sum(-1, keepdim=True)
        output = torch.zeros_like(flat)
        for index, expert in enumerate(self.experts):
            token, slot = torch.where(selected == index)
            # Each token appears at most once per expert; unique indexed writes
            # avoid nondeterministic scatter-add atomics on CUDA.
            contribution = expert(flat[token]) * weights[token, slot, None].to(flat.dtype)
            output[token] = output[token] + contribution
        mask = torch.ones(len(flat), device=x.device, dtype=torch.bool) if valid is None else valid.reshape(-1)
        counts = F.one_hot(selected[mask], len(self.experts)).float().sum((0, 1))
        fractions = counts / counts.sum().clamp_min(1)
        mean_probability = probability[mask].mean(0) if mask.any() else probability.mean(0)
        balance = len(self.experts) * (fractions * mean_probability).sum()
        return output.reshape(shape), balance, counts.detach()


class Attention(nn.Module):
    def __init__(self, c):
        super().__init__()
        self.heads = c.heads
        self.qkv = nn.Linear(c.width, 3*c.width, bias=False)
        self.out = nn.Linear(c.width, c.width, bias=False)
        frequency = 1 / (10000 ** (torch.arange(0, c.width//c.heads, 2).float() / (c.width//c.heads)))
        self.register_buffer('frequency', frequency, persistent=False)

    def rotate(self, x):
        angle = torch.outer(torch.arange(x.shape[-2], device=x.device), self.frequency)
        cosine, sine = angle.cos().to(x.dtype), angle.sin().to(x.dtype)
        a, b = x[..., 0::2], x[..., 1::2]
        return torch.stack((a*cosine-b*sine, a*sine+b*cosine), dim=-1).flatten(-2)

    def forward(self, x):
        batch, length, width = x.shape
        q, k, v = self.qkv(x).reshape(batch, length, 3, self.heads, width//self.heads).permute(2,0,3,1,4).unbind(0)
        values = F.scaled_dot_product_attention(self.rotate(q), self.rotate(k), v, dropout_p=0, is_causal=True)
        return self.out(values.transpose(1,2).contiguous().reshape(batch, length, width))


class Block(nn.Module):
    def __init__(self, c, index):
        super().__init__()
        self.attention_norm = nn.LayerNorm(c.width)
        self.attention = Attention(c)
        self.ff_norm = nn.LayerNorm(c.width)
        self.ff = Experts(c) if c.experts > 1 and index % 2 == 1 else FeedForward(c)

    def forward(self, x, valid=None):
        x = x + self.attention(self.attention_norm(x))
        if isinstance(self.ff, Experts):
            out, balance, counts = self.ff(self.ff_norm(x), valid)
        else:
            out, balance, counts = self.ff(self.ff_norm(x)), x.new_zeros(()), None
        return x + out, balance, counts


class LanguageModel(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.embedding = nn.Embedding(config.vocab_size, config.width)
        self.blocks = nn.ModuleList(Block(config, i) for i in range(config.layers))
        self.norm = nn.LayerNorm(config.width)
        self.apply(self.initialize)

    @staticmethod
    def initialize(module):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, std=.02)
            if getattr(module, 'bias', None) is not None: nn.init.zeros_(module.bias)

    def forward(self, ids, labels=None):
        if ids.shape[1] > self.config.context: raise ValueError('Context exceeds model configuration')
        x = self.embedding(ids)
        auxiliary, utilization = x.new_zeros(()), []
        for block in self.blocks:
            x, balance, counts = block(x, None if labels is None else labels != -100)
            auxiliary = auxiliary + balance
            if counts is not None: utilization.append(counts)
        logits = F.linear(self.norm(x), self.embedding.weight)
        loss = None if labels is None else F.cross_entropy(logits.reshape(-1, logits.shape[-1]), labels.reshape(-1), ignore_index=-100)
        if utilization: auxiliary = auxiliary / len(utilization)
        return logits, loss, auxiliary, utilization


def upcycle(dense, experts=4, top_k=2):
    if dense.config.experts != 1: raise ValueError('Upcycling requires a dense parent')
    config = Config(**(asdict(dense.config) | {'experts': experts, 'top_k': top_k}))
    sparse = LanguageModel(config).to(next(dense.parameters()).device)
    original = dense.state_dict()
    target = sparse.state_dict()
    for name in target:
        if '.ff.router.' in name: continue
        if '.ff.experts.' in name:
            prefix, suffix = name.split('.ff.experts.')
            name_from = prefix + '.ff.' + suffix.split('.', 1)[1]
        else: name_from = name
        target[name].copy_(original[name_from])
    sparse.load_state_dict(target)
    return sparse


def parameter_counts(config):
    model = LanguageModel(config)
    total = sum(p.numel() for p in model.parameters())
    inactive = sum(sum(p.numel() for p in block.ff.experts[0].parameters()) * (config.experts-config.top_k)
                   for block in model.blocks if isinstance(block.ff, Experts))
    return {'total': total, 'active_per_token_approximate': total-inactive}
