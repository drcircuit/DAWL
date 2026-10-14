"""Acceptance and immutable-policy boundaries, without using the live GPU."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import importlib.util
import os
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_finetune.execution import replay_outcome, recipe_with_policy, validate_recipe


class ReplayContractTests(unittest.TestCase):
    def test_bounded_epochs_require_new_strict_recipe(self):
        base={'schema':'recipe/1','epochs':3,'seed':7}
        one=recipe_with_policy(base,'strict-math',1)
        self.assertEqual(one['schema'],'hypertrix.local-lora-recipe/3')
        self.assertEqual(validate_recipe(base,one),'strict-math')
        self.assertEqual(base['epochs'],3)
        for count in (0,4,True):
            with self.assertRaises(ValueError):recipe_with_policy(base,'strict-math',count)
        with self.assertRaises(ValueError):recipe_with_policy(base,None,1)
    def test_timeout_does_not_erase_known_failure(self):
        self.assertEqual(replay_outcome([{'exact_match':False}],3,True),'mismatch')
        self.assertEqual(replay_outcome([{'exact_match':True}],3,True),'budget-incomplete')

    def test_all_boundaries_required_for_exact_pass(self):
        self.assertEqual(replay_outcome([{'exact_match':True}],2),'incomplete')
        self.assertEqual(replay_outcome([{'exact_match':True}]*2,2),'exact-match')
        with self.assertRaises(ValueError):replay_outcome([],0)

    def test_policy_is_a_new_recipe_and_cannot_be_substituted(self):
        base={'schema':'recipe/1','seed':7,'targets':['q_proj']};original=deepcopy(base)
        strict=recipe_with_policy(base,'strict-math')
        self.assertEqual(base,original)
        self.assertEqual(validate_recipe(base,strict),'strict-math')
        self.assertIsNone(validate_recipe(base,base))
        strict['execution_policy']['strict']=False
        with self.assertRaises(ValueError):validate_recipe(base,strict)

    def test_diagnostic_ablation_is_not_admitted_as_training_policy(self):
        with self.assertRaises(ValueError):recipe_with_policy({},'math-only')
        with self.assertRaises(ValueError):validate_recipe({'seed':1},{'seed':2})


@unittest.skipUnless(importlib.util.find_spec('torch'),'requires pinned training runtime')
class RuntimePolicyTests(unittest.TestCase):
    def test_strict_policy_fails_without_required_process_environment(self):
        from dawl_finetune.execution import configure
        with patch.dict(os.environ,{'CUBLAS_WORKSPACE_CONFIG':''}):
            with self.assertRaises(ValueError):configure('strict-math')

    def test_new_recipe_applies_measured_settings_and_preserves_legacy_contract(self):
        import torch
        from dawl_finetune.run import CONFIG,BASE_CONFIG,activate_recipe,seed,optimization_options,clipping_options
        original=deepcopy(BASE_CONFIG)
        with patch.dict(os.environ,{'CUBLAS_WORKSPACE_CONFIG':':4096:8'}):
            try:
                activate_recipe(recipe_with_policy(BASE_CONFIG,'strict-math'))
                settings=seed()
                self.assertTrue(torch.are_deterministic_algorithms_enabled())
                self.assertFalse(torch.is_deterministic_algorithms_warn_only_enabled())
                self.assertEqual(settings['sdpa_enabled'],{'math':True,'flash':False,'efficient':False,'cudnn':False})
                self.assertEqual(optimization_options(),{'foreach':False,'fused':False})
                self.assertEqual(clipping_options(),{'foreach':False})
            finally:
                activate_recipe(original)
                from dawl_finetune.execution import configure
                configure('legacy-auto')
        self.assertEqual(CONFIG,original)


if __name__=='__main__':unittest.main()
