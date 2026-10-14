"""Real tokenizer compatibility and signed source/model namespace boundaries."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src')); sys.path.insert(0,str(ROOT/'scripts'))
from dawl_node.common import canonical_json, keygen, load_key, sign
from dawl_profiles.profiles import make_o200k_profile, bind_o200k_model, ProfileTokenizer, validate_weights, content_id
from dawl_profiles.o200k_weights import verify_compatibility
from dawl_profiles.vectors import CONTRACT, validate_commitment
from cuda_gather import CudaGather


class O200kWeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rank=ROOT/'artifacts/tokenizer-candidates/o200k_base/o200k_base.tiktoken'
        base=ROOT/'artifacts/tokenizer-candidates/gpt-oss-20b'
        if not rank.exists() or not (base/'tokenizer.json').exists(): raise unittest.SkipTest('requires pinned public tokenizer assets')
        cls.ranks=rank.read_bytes(); cls.config=(base/'config.json').read_bytes(); cls.native=(base/'tokenizer.json').read_bytes()
        cls.source=make_o200k_profile(cls.ranks)
        cls.target=bind_o200k_model(cls.source,cls.ranks,cls.config,'input_embedding.weight','o200k_base-gpt-oss-20b-input')
        cls.compatibility,cls.reference=verify_compatibility(cls.source,cls.target,cls.ranks,cls.config,cls.native)

    def test_new_namespace_padded_model_rows_and_ordinary_controls(self):
        tokenizer=ProfileTokenizer(self.target,self.ranks,self.config)
        baseline=ProfileTokenizer(self.source,self.ranks)
        self.assertIsNone(self.source['body']['model']); self.assertNotEqual(self.target['id'],self.source['id'])
        self.assertEqual((tokenizer.rows,tokenizer.dimension),(201088,2880))
        self.assertEqual(self.compatibility['ordinary_rows_verified'],199998)
        for text in ['Hei æøå','cafe\u0301','你好','\x00','<|start|><|return|><|endofprompt|>']:
            _,ids=tokenizer.encode(text)
            self.assertEqual(ids,baseline.encode(text)[1]); self.assertEqual(ids,self.reference.encode(text,add_special_tokens=False).ids)
            self.assertTrue(all(i<199998 for i in ids))
        with self.assertRaises(ValueError): validate_weights({},self.source)

    def test_changed_token_row_pattern_and_config_rejected(self):
        changed=json.loads(self.native); changed['model']['vocab']['!']=123456
        with self.assertRaisesRegex(ValueError,'mapping differs'):
            verify_compatibility(self.source,self.target,self.ranks,self.config,canonical_json(changed))
        changed=json.loads(self.native); changed['pre_tokenizer']['pretokenizers'][0]['pattern']['Regex']='.'
        with self.assertRaisesRegex(ValueError,'contract differs'):
            verify_compatibility(self.source,self.target,self.ranks,self.config,canonical_json(changed))
        changed=json.loads(self.config); changed['vocab_size']=199998
        with self.assertRaisesRegex(ValueError,'cannot cover'):
            bind_o200k_model(self.source,self.ranks,canonical_json(changed),'input_embedding.weight','too-small')
        with self.assertRaisesRegex(ValueError,'config differs'):
            ProfileTokenizer(self.target,self.ranks,self.config+b' ')

    def test_signed_source_swap_and_implicit_namespace_reuse_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); public=keygen(root/'key',root/'pub'); key=load_key(root/'key')
            binding={'source_profile_id':self.source['id'],'execution_profile':self.target}
            body={'schema':'hypertrix.profile-vectors/2','profile_id':self.target['id'],'source_profile_id':self.source['id'],
                  'token_blueprint':'a'*64,'table_import':content_id(binding),'contract':CONTRACT,'books':[{}]}
            validate_commitment(sign(body,key),public,self.target,'a'*64,binding,self.source)
            for field,value in [('source_profile_id','b'*64),('profile_id',self.source['id']),('token_blueprint','c'*64)]:
                with self.assertRaises(ValueError):
                    validate_commitment(sign({**body,field:value},key),public,self.target,'a'*64,binding,self.source)
            with self.assertRaises(ValueError): validate_commitment(sign(body,key),public,self.target,'a'*64,binding)


@unittest.skipUnless(os.environ.get('DAWL_TEST_CUDA')=='1','requires isolated CUDA test container')
class O200kLargeCudaTests(unittest.TestCase):
    def test_real_table_shape_and_addressing_beyond_two_gib(self):
        # Synthetic addressing fixture, distinct from the actual pretrained run.
        matrix=np.zeros((201088,2880),dtype='<f4')
        matrix[0]=np.arange(2880,dtype='<f4'); matrix[199997]=-np.arange(2880,dtype='<f4'); matrix[-1]=42
        gpu=CudaGather(matrix)
        try:
            ids=[201087,199997,0,199997]
            self.assertEqual(gpu.payload(ids),matrix[ids].tobytes())
            self.assertGreater(gpu.runtime['device_allocation_bytes'],2*1024**3)
        finally: gpu.close()


if __name__=='__main__': unittest.main()
