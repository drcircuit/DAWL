"""Incremental admission cannot replace the baseline or grant auditor authority."""
import copy
import unittest
from test_cluster import fixture
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dawl_node.common import canonical_json,sha256,sign
from dawl_cluster.state import initial,advance,evaluate,AUDIT


class ContributionsTests(unittest.TestCase):
    def setUp(self):
        self.keys,self.public,plan=fixture();plan['embedding_dimension']=4
        self.state=initial(plan);advance(self.state,1)
        self.new=Ed25519PrivateKey.generate()
        for _ in range(2):
            for key in self.keys[:2]:
                result=self.send(key,'claim',{'capability':AUDIT})
                self.send(key,'result',dict(job=result['job'],generation=result['generation'],report=self.report('b'*64)))
        self.send(self.keys[3],'publish_dataset',{})
        self.old=copy.deepcopy(self.state['dataset_release'])
        self.activation=dict(version=2,implementation_sha256='f'*64,runtime_manifest='1'*64,tokenizer_sha256='2'*64,embedding_sha256='3'*64)
        self.announcement=dict(name='new-cortex',endpoint='http://127.70.0.22:8900',capabilities=['PROVIDER','TOKENIZER'])
        spec=dict(kind='full-shard-replay/v1',vectors_entry='4'*64,book_id=35,positions=[0],tokens=10,dense_bytes=160)
        self.proposal=dict(manifest=dict(sha256='5'*64,bytes=1000),catalog=dict(
            books=[dict(book_id=35,vectors_entry='4'*64,tokens_entry='6'*64,chunks=1,tokens=10,split='train',title='The Time Machine',source_sha256='7'*64)],
            jobs=[dict(id=sha256(canonical_json(spec)),spec=spec)],lineage_checkpoint=dict(policy='8'*64,entries=6,set_sha256='9'*64),
            training_manifest=dict(sha256='a'*64,bytes=100),**{k:v for k,v in self.activation.items() if k.endswith('_sha256')}))

    def report(self,implementation):
        return dict(outcome='pass',checked_chunks=1,tokens_checked=10,dense_bytes=160,implementation_sha256=implementation,execution_ms=1,error='')

    def send(self,key,kind,payload,ok=True,**extra):
        public=key.public_key().public_bytes_raw().hex()
        body=dict(schema='dawl.cluster-tx/1',chain_id=self.state['chain_id'],plan_sha256=self.state['plan_sha256'],
            sequence=self.state['actors'].get(public,{'sequence':0})['sequence']+1,valid_until=self.state['height']+60,kind=kind,payload=payload)
        tx=sign({**body,**extra},key)
        before=canonical_json(self.state);self.state,result=evaluate(self.state,tx)
        self.assertEqual(result['code'],0 if ok else 1,result)
        if not ok:self.assertEqual(before,canonical_json(self.state))
        return result.get('data')

    def activate(self):self.send(self.keys[3],'activate_contributions',self.activation)
    def join(self):self.activate();self.send(self.new,'announce_provider',self.announcement)

    def test_explicit_publisher_activation_and_role_separation(self):
        self.assertNotIn('providers',self.state)
        self.send(self.new,'announce_provider',self.announcement,False)
        self.send(self.keys[0],'activate_contributions',self.activation,False)
        self.join()
        self.send(self.keys[3],'activate_contributions',self.activation,False)
        self.send(self.new,'claim',{'capability':AUDIT},False)
        self.send(self.new,'publish_dataset',{},False)
        self.send(self.new,'announce_provider',self.announcement,False)
        self.assertEqual(self.old,self.state['dataset_release'])

    def test_join_rejects_role_escalation_duplicate_route_and_wrong_network(self):
        self.join()
        for change in ({'name':'another'},{'endpoint':'http://127.70.0.10:26657'},
                       {'capabilities':['AUDITOR']},{'name':'bad/name'}):
            self.send(Ed25519PrivateKey.generate(),'announce_provider',{**self.announcement,**change},False)
        self.send(Ed25519PrivateKey.generate(),'announce_provider',self.announcement,False,chain_id='other')

    def test_release_requires_distinct_audits_and_preserves_parent(self):
        self.join();self.send(self.new,'contribute',self.proposal)
        self.send(self.keys[3],'publish_dataset',{},False)
        for key in self.keys[:2]:
            lease=self.send(key,'claim',{'capability':AUDIT})
            self.send(key,'result',dict(job=lease['job'],generation=lease['generation'],report=self.report('f'*64)))
            if key==self.keys[0]:
                self.assertIsNone(self.send(key,'claim',{'capability':AUDIT})['job'])
                self.send(self.keys[3],'publish_dataset',{},False)
        self.send(self.keys[3],'publish_dataset',{})
        release=self.state['dataset_release']
        self.assertEqual(release['body']['parent_release'],self.old['id'])
        self.assertEqual(self.state['dataset_releases'],[self.old,release])
        self.assertEqual(len(release['body']['books']),2)
        self.send(self.new,'contribute',self.proposal,False)

    def test_catalog_rejects_missing_coverage_modified_recipe_and_duplicate_books(self):
        self.join()
        variants=[]
        for field,value in [('chunks',2),('tokens',20),('book_id',11)]:
            bad=copy.deepcopy(self.proposal);bad['catalog']['books'][0][field]=value;variants.append(bad)
        bad=copy.deepcopy(self.proposal);bad['catalog']['embedding_sha256']='0'*64;variants.append(bad)
        bad=copy.deepcopy(self.proposal);bad['catalog']['jobs']*=2;variants.append(bad)
        for bad in variants:self.send(self.new,'contribute',bad,False)
        self.send(Ed25519PrivateKey.generate(),'contribute',self.proposal,False)


class ArtifactTests(unittest.TestCase):
    def test_contributor_catalog_rebuilds_and_pins_shared_transform(self):
        from test_platform import PipelineTests
        from dawl_cluster.contribution_artifacts import catalog,import_manifest
        from dawl_cluster.contributions import validate_catalog
        from dawl_node.common import read_json
        pipeline=PipelineTests();pipeline.setUp();self.addCleanup(pipeline.doCleanups)
        description=catalog(pipeline.ledger,pipeline.objects)
        state={'plan':{'books':[],'embedding_dimension':4},'jobs':{},'protocol_upgrade':description}
        validate_catalog(state,description)
        key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes_raw().hex()
        trust=read_json(pipeline.root/'trust.json')
        envelope=sign(dict(schema='hypertrix.contribution-manifest/1',catalog=description,trust=trust,lineage=list(pipeline.ledger.entries())),key)
        batch=dict(provider=public,catalog=description)
        imported=import_manifest(pipeline.base/'replica',envelope,batch,trust['anchors'])
        self.assertEqual(catalog(imported,pipeline.objects),description)
        with self.assertRaisesRegex(ValueError,'transform'):
            import_manifest(pipeline.base/'bad',envelope,batch,{**trust['anchors'],'embedding_sha256':'0'*64})
        with self.assertRaisesRegex(ValueError,'catalog'):
            import_manifest(pipeline.base/'bad2',envelope,{**batch,'catalog':{}},trust['anchors'])


if __name__=='__main__':unittest.main()
