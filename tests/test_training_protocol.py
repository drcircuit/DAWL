import copy
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dawl_node.common import keygen,load_key,sign,pack_ids
from dawl_platform.artifacts import Objects
from dawl_profiles.profiles import content_id
from dawl_training.data import batch,make_dataset
from dawl_training.protocol import (allocation,schedule,validate_stage,validate_update,aggregate,
                                    round_body,certify,verify_round)


class TrainingProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.objects=Objects(self.root/'objects'); self.keys=[]; peers=[]
        for i in range(2):
            public=keygen(self.root/f'key-{i}',self.root/f'pub-{i}'); self.keys.append(load_key(self.root/f'key-{i}'))
            peers.append({'id':i,'public':public})
        d={'sha256':'a'*64,'bytes':1}
        pb={'schema':'hypertrix.model-profile/1','label':'test','tokenizer':{'artifact':d,'library':'tokenizers',
            'version':'0.23.3','contract':'independent-utf8-frags/no-auto-special-tokens/literal-added-tokens/v1','normalization':'none'},
            'model':{'config':d,'family':'llama','input_embedding':{'name':'input_embedding.weight','shape':[16,8]}},
            'chunking':{'part_bytes':524288,'frag_bytes':4096,'boundary':'utf8-codepoint/v1'}}
        self.initial=self.objects.put(np.zeros(4,dtype='<f4').tobytes())
        layout=[{'name':'test.B','shape':[2,2],'offset':0,'elements':4}]
        self.dataset={'train':[{'count':i+1,'index':i} for i in range(8)]}
        b={'schema':'hypertrix.training-stage/1','profile':{'id':content_id(pb),'body':pb},'participants':peers,
            'dataset':self.objects.json(self.dataset),'initial_adapter':self.initial,'fixed_projection':self.initial,
            'adapter_layout':layout,'projection_layout':layout,'recipe':{
                'algorithm':'fixed-A-LoRA/local-SGD/unanimous-token-weighted-mean/v1','optimizer':'SGD/no-momentum/no-weight-decay',
                'dtype':'float32','rank':8,'alpha':16,'targets':['q_proj','v_proj'],'seed':20261014,
                'sequence_length':128,'batch_size':2,'rounds':2,'local_steps':1,'learning_rate':0.1,
                'clip_grad_norm':1.0,'max_update_l2':2.0}}
        self.stage={'id':content_id(b),'body':b}; validate_stage(self.stage)
        self.updates=[]
        for i in range(2):
            desc=self.objects.put(np.full(4,(i+1)*0.1,dtype='<f4').tobytes())
            self.updates.append(sign({'schema':'hypertrix.training-update/1',
                'allocation':allocation(self.stage,self.dataset,0,i,self.stage['id']),
                'adapter':desc,'loss':2.0,'seconds':1.0,'device':'synthetic-test'},self.keys[i]))

    def test_shards_are_disjoint_and_parent_bound(self):
        all_records=[r['index'] for round in range(2) for peer in range(2) for r in schedule(self.dataset,self.stage,round,peer)]
        self.assertEqual(all_records,list(range(8)))
        for field,value in [('parent','b'*64),('stage','c'*64),('round',1),('data','d'*64),('target_tokens',1000)]:
            body=copy.deepcopy(self.updates[0]['body']); body['allocation'][field]=value
            with self.assertRaises(ValueError): validate_update(sign(body,self.keys[0]),self.stage,self.dataset,0,0,self.stage['id'])

    def test_all_replicas_recompute_same_weighted_round(self):
        payload=aggregate(self.stage,self.dataset,0,self.stage['id'],self.initial,self.updates,self.objects)
        actual=np.frombuffer(payload,dtype='<f4')
        np.testing.assert_array_equal(actual,np.full(4,(0.1*3+0.2*7)/10,dtype='<f4'))
        desc=self.objects.put(payload); body=round_body(self.stage,0,self.stage['id'],self.updates,desc)
        record=certify(body,[sign(body,k) for k in self.keys],self.stage)
        self.assertEqual(verify_round(record,self.stage,self.dataset,0,self.stage['id'],self.initial,self.updates,self.objects),desc)
        with self.assertRaises(ValueError): certify(body,record['acceptances'][:1],self.stage)
        wrong={**body,'parent':'f'*64}
        with self.assertRaises(ValueError): certify(body,[sign(body,self.keys[0]),sign(wrong,self.keys[1])],self.stage)
        bad=copy.deepcopy(record); bad['body']['adapter']=self.initial
        with self.assertRaises(ValueError): verify_round(bad,self.stage,self.dataset,0,self.stage['id'],self.initial,self.updates,self.objects)

    def test_bad_updates_and_duplicate_identity_rejected(self):
        for values in ([float('nan')]*4,[float('inf')]*4,[10.]*4,[0.]*3):
            body=copy.deepcopy(self.updates[0]['body']); body['adapter']=self.objects.put(np.array(values,dtype='<f4').tobytes())
            bad=[sign(body,self.keys[0]),self.updates[1]]
            with self.assertRaises(ValueError): aggregate(self.stage,self.dataset,0,self.stage['id'],self.initial,bad,self.objects)
        for updates in (self.updates[:1],[self.updates[0],self.updates[0]],list(reversed(self.updates))):
            with self.assertRaises(ValueError): aggregate(self.stage,self.dataset,0,self.stage['id'],self.initial,updates,self.objects)

    def test_next_token_masks_no_cross_frag_or_double_shift(self):
        desc=self.objects.put(pack_ids([2,4,6,8]))
        x,y=batch([{'tokens':desc,'offset':0,'count':3}],self.objects,16)
        np.testing.assert_array_equal(x[0,:3],[2,4,6]); np.testing.assert_array_equal(y[0,:3],[4,6,8])
        self.assertTrue((y[0,3:]==-100).all())
        with self.assertRaises(ValueError): batch([{'tokens':desc,'offset':1,'count':3}],self.objects,16)
        with self.assertRaises(ValueError): batch([{'tokens':desc,'offset':0,'count':3}],self.objects,8)

    def test_book_holdouts_and_complete_window_coverage(self):
        entries=[]
        for book in (11,84,1342):
            records=[]
            for frag,count in enumerate((1,4,130)):
                records.append({'index':frag,'count':count,'tokens':self.objects.put(pack_ids([2]*count))})
            manifest=self.objects.json({'records':records})
            entries.append({'id':str(book),'body':{'payload':{'data':{'book_id':book,'manifest':manifest}}}})
        dataset=make_dataset(self.objects,entries)
        self.assertEqual(dataset,make_dataset(self.objects,list(reversed(entries))))
        for split,book in [('train',11),('validation',1342),('test',84)]:
            self.assertEqual({r['book'] for r in dataset[split]},{book})
            self.assertEqual(sum(r['count'] for r in dataset[split]),132)
            self.assertEqual(len(dataset[split]),3)


if __name__=='__main__': unittest.main()
