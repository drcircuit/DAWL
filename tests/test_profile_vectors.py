"""Import/commitment boundaries, including correctly signed malicious claims."""
import copy
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dawl_node.common import canonical_json, keygen, load_key, sign, sha256
from dawl_profiles.profiles import describe, content_id
from dawl_profiles.workspace import Workspace
from dawl_profiles.vectors import (CONTRACT, import_embedding, materialize, widen,
                                   validate_commitment, verify_frag)
from cuda_gather import CudaGather
from test_profiles import fixture


class ProfileVectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.ws=Workspace(self.root)
        self.profile=fixture(self.ws,'vector',dimension=7)
        self.matrix=(np.arange(264*7,dtype='<f4')/128).reshape(264,7)
        raw=self.matrix.astype('<f2').tobytes()
        header=canonical_json({'upstream.embed':{'dtype':'F16','shape':[264,7],'data_offsets':[0,len(raw)]}})
        payload=struct.pack('<Q',len(header))+header+raw
        self.path=self.root/'model.safetensors'; self.path.write_bytes(payload)
        self.upstream={**describe(payload),'tensor':'upstream.embed','repo':'fixture','revision':'fixture'}
        self.binding=import_embedding(self.ws.objects,self.profile,self.path,self.upstream)
        self.public=keygen(self.root/'key.pem',self.root/'pub'); self.key=load_key(self.root/'key.pem')
        self.body={'schema':'hypertrix.profile-vectors/1','profile_id':self.profile['id'],
            'token_blueprint':'a'*64,'table_import':content_id(self.binding),'contract':CONTRACT,'books':[{}]}

    def test_verified_import_widen_and_reassemble(self):
        table=materialize(self.ws.objects,self.profile,self.binding,self.root/'table.bin')
        self.assertEqual(table.tobytes(),self.matrix.tobytes())
        table._mmap.close()
        self.assertEqual(self.binding['upstream_tensor']['dtype'],'F16')
        self.assertFalse(self.binding['training_history_verified'])
        bits=np.array([0,0x8000,0x0001,0x3f80,0xbf80,0x7f7f],dtype='<u2')
        widened=widen(bits.tobytes(),'BF16')
        self.assertEqual(widened,(bits.astype('<u4')<<16).tobytes())
        for dtype,payload in [('BF16',struct.pack('<H',0x7fc1)),('F16',struct.pack('<H',0x7c00)),('F32',struct.pack('<I',0x7f800000))]:
            with self.assertRaisesRegex(ValueError,'nonfinite'): widen(payload,dtype)

    def test_wrong_checkpoint_tensor_and_corrupt_chunk_rejected(self):
        wrong={**self.upstream,'sha256':'f'*64}
        with self.assertRaisesRegex(ValueError,'hash mismatch'): import_embedding(self.ws.objects,self.profile,self.path,wrong)
        wrong={**self.upstream,'tensor':'wrong.name'}
        with self.assertRaisesRegex(ValueError,'missing'): import_embedding(self.ws.objects,self.profile,self.path,wrong)
        chunk=self.binding['weights']['tensors']['model.embed_tokens.weight']['chunks'][0]
        (self.ws.objects.root/chunk['sha256']).write_bytes(b'X'*chunk['bytes'])
        with self.assertRaisesRegex(ValueError,'hash or size'): materialize(self.ws.objects,self.profile,self.binding,self.root/'bad.bin')

    def test_signed_namespace_blueprint_table_and_contract_substitution_rejected(self):
        validate_commitment(sign(self.body,self.key),self.public,self.profile,'a'*64,self.binding)
        for field,value in [('profile_id','b'*64),('token_blueprint','c'*64),('table_import','d'*64),('contract','approximate')]:
            wrong={**self.body,field:value}
            with self.assertRaisesRegex(ValueError,'binding mismatch'):
                validate_commitment(sign(wrong,self.key),self.public,self.profile,'a'*64,self.binding)

    def test_token_commitment_wrong_hash_and_changed_bit_rejected(self):
        actual=self.matrix[[0,263,2,2]].tobytes()
        token={'index':0,'tokens':describe(struct.pack('<4I',0,263,2,2)),'count':4}
        record={**token,'vectors':describe(actual)}
        verify_frag(actual,actual,record,token,7)
        wrong=copy.deepcopy(record); wrong['vectors']['sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'commitment mismatch'): verify_frag(actual,actual,wrong,token,7)
        wrong=copy.deepcopy(record); wrong['tokens']['sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'token binding'): verify_frag(actual,actual,wrong,token,7)
        changed=bytes([actual[0]^1])+actual[1:]
        with self.assertRaisesRegex(ValueError,'commitment mismatch'): verify_frag(changed,actual,record,token,7)


@unittest.skipUnless(os.environ.get('DAWL_TEST_CUDA')=='1','requires isolated CUDA test container')
class ProfileCudaTests(unittest.TestCase):
    def test_table_above_old_256mib_limit_and_boundary_rows(self):
        # Exercises the widened bound and row-addressing on actual hardware.
        matrix=np.zeros((65537,1024),dtype='<f4')
        matrix[0]=np.arange(1024,dtype='<f4'); matrix[-1]=np.arange(1024,dtype='<f4')*-2
        gpu=CudaGather(matrix)
        try:
            ids=[65536,0,65536,32768]
            self.assertEqual(gpu.payload(ids),matrix[ids].tobytes())
            self.assertFalse(gpu.runtime['cpu_fallback'])
            with self.assertRaises(ValueError): gpu.payload([65537])
        finally: gpu.close()


if __name__=='__main__': unittest.main()
