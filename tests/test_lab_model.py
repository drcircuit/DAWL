import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import torch
from dawl_lab.model import Config,LanguageModel
from dawl_lab.run import configure,optimizer_for,update,save_checkpoint,load_checkpoint,attest
from dawl_node.common import write_json,keygen


class LabModelTests(unittest.TestCase):
    def setUp(self):
        configure('cpu','fp32','math',2);torch.manual_seed(4)
        self.config=Config(vocab_size=260,context=24,width=32,heads=4,kv_heads=2,layers=2,hidden=64)

    def test_causal_mask_and_cached_decoding(self):
        model=LanguageModel(self.config).eval();ids=torch.randint(3,260,(2,12));changed=ids.clone();changed[:,7:]=3
        with torch.no_grad():
            full=model(ids)[0]
            torch.testing.assert_close(full[:,:7],model(changed)[0][:,:7],atol=0,rtol=0)
            first,_,cache=model(ids[:,:7],use_cache=True)
            rest,_,cache=model(ids[:,7:10],cache=cache,use_cache=True)
            last,_,cache=model(ids[:,10:11],cache=cache,use_cache=True)
            torch.testing.assert_close(rest,full[:,7:10],atol=2e-6,rtol=1e-5)
            torch.testing.assert_close(last,full[:,10:11],atol=2e-6,rtol=1e-5)
            self.assertEqual(cache[0][0].shape,(2,2,11,8))

    def test_gradients_and_exact_checkpoint_resume(self):
        model=LanguageModel(self.config);values=np.random.default_rng(1).integers(3,260,(4,25));values[1,19:]=2
        recipe={'learning_rate':.001,'pad_id':2,'steps':4,'micro_batch':2,'runtime':{'precision':'fp32','attention':'math'}}
        opt=optimizer_for(model,recipe);update(model,opt,values,np.array([0,1]),recipe,0,'cpu')
        self.assertGreater(float(model.blocks[0].attention.k.weight.grad.abs().sum()),0)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);write_json(root/'recipe.json',recipe);write_json(root/'config.json',self.config.__dict__)
            keygen(root/'operator.private.pem',root/'operator.public.key');attest(root,'initialization',{})
            save_checkpoint(root,model,opt,1)
            restored,resumed,_=load_checkpoint(root,1,'cpu',True)
            update(model,opt,values,np.array([2,3]),recipe,1,'cpu');update(restored,resumed,values,np.array([2,3]),recipe,1,'cpu')
            for key,value in model.state_dict().items():self.assertTrue(torch.equal(value,restored.state_dict()[key]),key)
            # Corruption must fail before weights are used.
            path=root/'checkpoints/0000001/state.safetensors';raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            with self.assertRaises(ValueError):load_checkpoint(root,1)

    def test_accumulation_uses_target_counts(self):
        model=LanguageModel(self.config);other=LanguageModel(self.config);other.load_state_dict(model.state_dict())
        values=np.random.default_rng(2).integers(3,260,(4,25));values[1,5:]=2
        recipe={'learning_rate':.001,'pad_id':2,'steps':4,'micro_batch':1,'runtime':{'precision':'fp32','attention':'math'}}
        # Compare gradients directly, avoiding Adam's amplification near zero.
        class Capture:
            param_groups=[{}]
            def __init__(self,m):self.m=m
            def zero_grad(self,**kw):self.m.zero_grad(**kw)
            def step(self):pass
        update(model,Capture(model),values,np.arange(4),recipe,0,'cpu')
        update(other,Capture(other),values,np.arange(4),{**recipe,'micro_batch':4},0,'cpu')
        for a,b in zip(model.parameters(),other.parameters()):torch.testing.assert_close(a.grad,b.grad,atol=2e-6,rtol=1e-4)

    def test_invalid_grouped_query_shape(self):
        with self.assertRaises(ValueError):Config(width=32,heads=4,kv_heads=3)


if __name__=='__main__':unittest.main()
