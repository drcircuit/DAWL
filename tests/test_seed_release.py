from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_seed_release import safe,require,verify,digest
from dawl_node.common import keygen,load_key,sign,write_json


class ReleaseBoundaryTests(unittest.TestCase):
    def test_artifact_paths_cannot_leave_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertEqual(safe(root,'nested/checkpoint.json'),root/'nested/checkpoint.json')
            with self.assertRaises(ValueError):safe(root,'../outside')

    def test_integrity_failure_is_not_an_assertion(self):
        with self.assertRaises(ValueError):require(False,'Rejected')

    def fixture(self,root,optimizer_exact=True,include_replay=True):
        """Small signed metadata fixture; no assertion that these are trained weights."""
        data=root/'dataset';model=root/'dense';data.mkdir();model.mkdir()
        keygen(model/'operator.private.pem',model/'operator.public.key')
        key=load_key(model/'operator.private.pem');public=(model/'operator.public.key').read_text().strip()
        (data/'verifier.public.key').write_text(public)
        def put(name,value):
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);write_json(path,value);return digest(path)
        source={'intake_lock_sha256':put('intake/source-lock.json',{}),'catalogue_snapshots_sha256':put('intake/downloads.json',[])}
        bp={'parent':'parent-anchor','domains':{'norwegian':1},'documents':2,'arrays':{}}
        bp['sources_sha256']=put('dataset/sources.json',source)
        for name in ('windows','tokenizer','evaluation','group-policy'):
            field=name.replace('-','_')+'_sha256';bp[field]=put('dataset/'+name+'.json',{})
        blueprint=put('dataset/blueprint.json',bp)
        (model/'implementation').mkdir();(model/'dependencies').mkdir()
        (model/'implementation/intake.py').write_text('# Fixture extractor\n')
        (data/'verifier-implementation.py').write_text('# Fixture verifier\n')
        body={'accepted':True,'blueprint_sha256':blueprint,'parent_blueprint_sha256':bp['parent'],
            'group_policy_sha256':bp['group_policy_sha256'],'new_documents_reconstructed':1,'legacy_documents_unchanged':1,
            'author_groups_disjoint':True,'canonical_identities_disjoint':True,'exact_content_splits_disjoint':True,
            'legacy_arrays_and_evaluation_exact':True,'verifier_sha256':digest(data/'verifier-implementation.py'),
            'extractor_sha256':digest(model/'implementation/intake.py')}
        receipt=sign(body,key);put('dataset/data-verification.json',receipt)
        initial={'parent':None}
        for name,value in [('config',{}),('recipe',{'blueprint_sha256':blueprint,'max_steps':2}),
            ('implementation',{'intake.py':body['extractor_sha256']}),('dependencies',{})]:
            initial[name+'_sha256']=put('dense/'+name+'.json',value)
        records=[]
        def record(kind,payload):
            records.append(sign({'sequence':len(records),'previous':records[-1]['id'] if records else None,'kind':kind,'payload':payload},key))
        record('initialization',initial)
        for step in (0,2):
            desc={'step':step,'config_sha256':initial['config_sha256'],'recipe_sha256':initial['recipe_sha256'],
                'evaluation':{'mean_domain_nll':1.0} if step else None,'state_sha256':'fixture-no-tensors'}
            path=f'checkpoints/{step:07d}/checkpoint.json';record('checkpoint',{'path':path,'sha256':put('dense/'+path,desc)})
        summary={'selected_step':2,'completed_steps':2,'planned_steps':2,'validation_mean_nll':1.0,'blueprint_sha256':blueprint}
        record('training-result',{'sha256':put('dense/summary.json',summary)})
        replay={'start_step':0,'end_step':2,'accepted':True,'optimizer_exact':optimizer_exact,'max_absolute':0,
            'exact_tensors':1,'tensor_count':1,'checks':[{'name':'fixture','exact':True,'max_absolute':0}]}
        replay_hash=put('dense/replay-0000000-0000002.json',replay)
        if include_replay:record('replay',{'start_step':0,'end_step':2,'sha256':replay_hash})
        (model/'lineage.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in records))
        return {'blueprint_sha256':blueprint,'data_verification':receipt,'data_verifier_public_key':public,
            'operator_public_key':public,'lineage_sha256':digest(model/'lineage.jsonl'),'summary':summary}

    def test_signature_chain_and_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);report=self.fixture(root)
            checked=verify(root,report,metadata_only=True)
            self.assertEqual(checked['replay_intervals'],[{'start':0,'end':2,'tensors':1}])
            (root/'dense/checkpoints/0000002/checkpoint.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Checkpoint descriptor differs'):verify(root,report,metadata_only=True)

    def test_missing_or_rejected_numerical_evidence_blocks_release(self):
        for options in ({'include_replay':False},{'optimizer_exact':False}):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);report=self.fixture(root,**options)
                with self.assertRaisesRegex(ValueError,'Initialization replay missing|Replay rejected'):
                    verify(root,report,metadata_only=True)
