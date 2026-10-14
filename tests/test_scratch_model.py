import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
import torch
from dawl_node.common import keygen, write_json
from dawl_scratch.model import Config, LanguageModel, upcycle
from dawl_scratch.run import configure, make_optimizer, update, save_checkpoint, load_checkpoint
from dawl_scratch.data import assign_groups


class ModelTests(unittest.TestCase):
    def setUp(self):
        configure('cpu');torch.manual_seed(123)
        self.config=Config(vocab_size=260,context=16,width=32,heads=4,layers=2,hidden=64)

    def test_causality_and_upcycle_and_expert_gradients(self):
        dense=LanguageModel(self.config)
        ids=torch.randint(3,260,(2,12))
        altered=ids.clone();altered[:,6:]=3
        self.assertTrue(torch.equal(dense(ids)[0][:,:6],dense(altered)[0][:,:6]))
        sparse=upcycle(dense)
        self.assertTrue(torch.allclose(dense(ids)[0],sparse(ids)[0],atol=1e-6,rtol=1e-6))
        _,loss,balance,counts=sparse(ids,ids)
        (loss+.01*balance).backward()
        self.assertTrue(torch.isfinite(sparse.blocks[1].ff.router.weight.grad).all())
        self.assertGreater(sparse.blocks[1].ff.router.weight.grad.abs().sum(),0)
        self.assertEqual(int(counts[0].sum()),2*ids.numel())

    def test_checkpoint_resume_reproduces_optimizer_and_tensors(self):
        recipe={'learning_rate':.001,'pad_id':2,'max_steps':10,'router_balance_weight':.01}
        values=np.random.default_rng(1).integers(3,260,size=(4,17),dtype=np.int64)
        values[0,10:]=2
        model=LanguageModel(self.config);opt=make_optimizer(model,recipe)
        update(model,opt,values,[0,1],recipe,0,'cpu')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            write_json(root/'recipe.json',recipe)
            write_json(root/'config.json',self.config.__dict__)
            keygen(root/'operator.private.pem',root/'operator.public.key')
            save_checkpoint(root,model,opt,1)
            restored,resumed,_=load_checkpoint(root,1,'cpu',True)
            update(model,opt,values,[2,3],recipe,1,'cpu')
            update(restored,resumed,values,[2,3],recipe,1,'cpu')
            for name,value in model.state_dict().items():
                self.assertTrue(torch.equal(value,restored.state_dict()[name]),name)
            with (root/'checkpoints/0000001/state.safetensors').open('ab') as stream:stream.write(b'corruption')
            with self.assertRaisesRegex(ValueError,'bytes differ'):load_checkpoint(root,1)

    def test_holdout_groups_stable(self):
        result=assign_groups(['a','b','a','c','d'])
        self.assertEqual(result,assign_groups(['d','c','b','a']))
        self.assertEqual(set(result.values()),{'train','validation','test'})


if __name__=='__main__':unittest.main()
