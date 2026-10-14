import copy
from pathlib import Path
import tempfile
import unittest
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dawl_platform.artifacts import Objects
from dawl_training.work import catalog, records, reduce_updates, model_contract


class WorkTests(unittest.TestCase):
    def test_model_contract_preserves_weight_meaning_across_continuations(self):
        manifest = {'profile': {'id': 'a'*64}, 'parent_model': {'sha256': 'b'*64},
                    'fixed_projection': {'sha256': 'c'*64, 'bytes': 8},
                    'projection_layout': [{'name': 'A', 'shape': [1, 2]}],
                    'adapter_layout': [{'name': 'B', 'shape': [2, 1]}],
                    'initial_adapter': {'sha256': 'd'*64, 'bytes': 8}}
        before = model_contract(manifest)
        manifest['initial_adapter']['sha256'] = 'e'*64
        self.assertEqual(before, model_contract(manifest))
        for name, value in [('fixed_projection', {'sha256': '0'*64, 'bytes': 8}),
                            ('adapter_layout', [{'name': 'B', 'shape': [1, 2]}])]:
            changed = {**manifest, name: value}
            self.assertNotEqual(before, model_contract(changed))

    def test_catalog_binds_disjoint_windows_and_valid_targets(self):
        data = {'train': [{'window':i, 'count':i+1} for i in range(16)]}
        work = catalog(data, 2, 2, 2)
        self.assertEqual([w['start'] for w in work], [0,4,8,12])
        self.assertEqual(sum(w['target_tokens'] for w in work), sum(range(1,17)))
        self.assertEqual(records(data,work[1]), data['train'][4:8])
        bad = copy.deepcopy(data); bad['train'][4]['count'] += 1
        with self.assertRaises(ValueError): records(bad,work[1])
        with self.assertRaises(ValueError): catalog(data,3,2,2)
        bad = copy.deepcopy(data); bad['train'][4] = bad['train'][0]
        with self.assertRaises(ValueError): catalog(bad,2,2,2)

    def test_reduction_rejects_poison_shape_norm_and_noncanonical_order(self):
        with tempfile.TemporaryDirectory() as temp:
            objects = Objects(Path(temp)/'objects')
            initial = objects.put(np.zeros(2,dtype='<f4').tobytes())
            jobs = [{'spec':{'shard':i,'target_tokens':weight},
                     'contribution':{'adapter':objects.put(np.full(2,value,dtype='<f4').tobytes())}}
                    for i,(weight,value) in enumerate([(1,.125),(3,.375)])]
            actual = np.frombuffer(reduce_updates(objects,initial,jobs),dtype='<f4')
            np.testing.assert_array_equal(actual,np.array([.3125,.3125],dtype='<f4'))
            with self.assertRaises(ValueError):reduce_updates(objects,initial,jobs[::-1])
            for payload in ([float('nan'),0],[float('inf'),0],[3,0],[.1]):
                bad = copy.deepcopy(jobs); bad[0]['contribution']['adapter'] = objects.put(np.array(payload,dtype='<f4').tobytes())
                with self.assertRaises(ValueError):reduce_updates(objects,initial,bad)
