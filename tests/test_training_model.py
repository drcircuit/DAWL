"""Small numerical checks; real full-model replay is exercised by the pilot."""
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))


@unittest.skipUnless(importlib.util.find_spec('torch') and importlib.util.find_spec('transformers'),'requires training runtime')
class AdapterTests(unittest.TestCase):
    def test_only_B_is_trained_and_frozen_A_makes_linear_averaging_valid(self):
        import torch
        from dawl_training.model import FixedAdapter
        torch.manual_seed(6); base=torch.nn.Linear(5,3,bias=False); base.requires_grad_(False)
        layer=FixedAdapter(base,torch.Generator().manual_seed(7),rank=2)
        x=torch.randn(4,5); frozen=base.weight.detach().clone(); A=layer.A.clone()
        self.assertTrue(torch.equal(layer(x),base(x)))
        optimizer=torch.optim.SGD([layer.B],lr=0.1)
        optimizer.zero_grad(); (layer(x)-1).square().mean().backward(); optimizer.step()
        self.assertGreater(float(layer.B.detach().abs().sum()),0)
        self.assertTrue(torch.equal(frozen,base.weight)); self.assertTrue(torch.equal(A,layer.A))
        with torch.no_grad():
            one=layer.B.clone(); y1=layer(x).clone()
            layer.B.copy_(one*2); y2=layer(x).clone()
            layer.B.copy_(one*.25+one*2*.75)
            torch.testing.assert_close(layer(x),y1*.25+y2*.75,atol=1e-6,rtol=1e-5)


if __name__=='__main__': unittest.main()
