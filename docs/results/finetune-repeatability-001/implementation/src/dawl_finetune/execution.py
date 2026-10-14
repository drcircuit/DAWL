"""Explicit execution policies for new runs; never retrofit a sealed recipe."""
import os

POLICIES = {
    'legacy-auto': {'attention_backend': 'auto', 'strict': False, 'explicit_optimizer': False},
    'math-only': {'attention_backend': 'math', 'strict': False, 'explicit_optimizer': False},
    'strict-auto': {'attention_backend': 'auto', 'strict': True, 'explicit_optimizer': True},
    'strict-math': {'attention_backend': 'math', 'strict': True, 'explicit_optimizer': True},
}


def configure(name):
    import torch
    policy = POLICIES[name]
    strict = policy['strict']
    if strict and os.environ.get('CUBLAS_WORKSPACE_CONFIG') != ':4096:8':
        raise ValueError('Strict policy requires CUBLAS_WORKSPACE_CONFIG=:4096:8 before CUDA initialization')
    torch.use_deterministic_algorithms(strict, warn_only=False)
    torch.backends.cudnn.deterministic = strict
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = not strict
    torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = not strict
    torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = not strict
    math_only = policy['attention_backend'] == 'math'
    torch.backends.cuda.enable_math_sdp(True)
    torch.backends.cuda.enable_flash_sdp(not math_only)
    torch.backends.cuda.enable_mem_efficient_sdp(not math_only)
    torch.backends.cuda.enable_cudnn_sdp(not math_only)
    return {
        'schema': 'hypertrix.execution-policy/1', 'name': name, **policy,
        'deterministic_algorithms': torch.are_deterministic_algorithms_enabled(),
        'deterministic_warn_only': torch.is_deterministic_algorithms_warn_only_enabled(),
        'cublas_workspace_config': os.environ.get('CUBLAS_WORKSPACE_CONFIG'),
        'cudnn_deterministic': torch.backends.cudnn.deterministic,
        'cudnn_benchmark': torch.backends.cudnn.benchmark,
        'matmul_tf32': torch.backends.cuda.matmul.allow_tf32,
        'cudnn_tf32': torch.backends.cudnn.allow_tf32,
        'bf16_reduced_precision_reduction': torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction,
        'fp16_reduced_precision_reduction': torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction,
        'sdpa_enabled': {'math': torch.backends.cuda.math_sdp_enabled(),
                         'flash': torch.backends.cuda.flash_sdp_enabled(),
                         'efficient': torch.backends.cuda.mem_efficient_sdp_enabled(),
                         'cudnn': torch.backends.cuda.cudnn_sdp_enabled()},
        'optimizer_foreach': False if policy['explicit_optimizer'] else None,
        'optimizer_fused': False if policy['explicit_optimizer'] else None,
    }


def optimizer_options(name):
    return {'foreach': False, 'fused': False} if POLICIES[name]['explicit_optimizer'] else {}


def replay_outcome(checks, planned, budget_stop=False):
    """A known mismatch cannot be hidden by a subsequent timeout."""
    if planned < 1 or len(checks) > planned:
        raise ValueError('Invalid number of replay boundaries')
    if any(c['exact_match'] is False for c in checks):
        return 'mismatch'
    if len(checks) == planned and all(c['exact_match'] is True for c in checks):
        return 'exact-match'
    return 'budget-incomplete' if budget_stop else 'incomplete'


def recipe_with_policy(baseline, name=None):
    """A new immutable recipe is required to change execution semantics."""
    from copy import deepcopy
    recipe=deepcopy(baseline)
    if name is not None:
        if name not in ('strict-auto','strict-math'):
            raise ValueError('Training accepts only strict policies; ablations are diagnostic only')
        recipe['schema']='hypertrix.local-lora-recipe/2'
        recipe['execution_policy']={'name':name,**POLICIES[name],
                                    'cublas_workspace_config':':4096:8',
                                    'fail_on_nondeterminism':True}
    return recipe


def validate_recipe(baseline, recipe):
    name=recipe.get('execution_policy',{}).get('name')
    if recipe != recipe_with_policy(baseline,name):
        raise ValueError('Recipe is not the supported frozen configuration')
    return name
