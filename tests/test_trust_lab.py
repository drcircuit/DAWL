"""Isolated HTTP/control tests. Fixtures never enter the live study store."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        spec=importlib.util.spec_from_file_location('trust_lab_test_instance',ROOT/'scripts/trust_lab.py')
        self.lab=importlib.util.module_from_spec(spec)
        with patch.object(sys,'argv',['trust_lab.py','--pilot',str(self.root/'pilot'),'--root',str(self.root/'lab')]):spec.loader.exec_module(self.lab)
        self.lab.args.pilot.mkdir();self.lab.KEY_FILE=self.root/'no-key'
        self.client=TestClient(self.lab.app,base_url='http://127.0.0.1:8885')
        self.headers={'X-Lab-Token':self.lab.TOKEN}
        self.keypatch=patch.object(self.lab,'load_key',return_value='');self.keypatch.start()

    def tearDown(self):self.keypatch.stop();self.temp.cleanup()

    def test_local_origin_and_csrf_and_private_file_boundaries(self):
        self.assertEqual(self.client.post('/api/chat',json={'model_id':'openai','message':'hello'}).status_code,403)
        self.assertEqual(self.client.post('/api/chat',headers={**self.headers,'Origin':'https://unrelated.example'},json={'model_id':'openai','message':'hello'}).status_code,403)
        self.assertEqual(self.client.get('/api/status',headers={'Host':'unrelated.example'}).status_code,403)
        (self.lab.args.pilot/'operator.private.pem').write_text('TEST-ONLY private key fixture')
        self.assertEqual(self.client.get('/evidence/operator.private.pem').status_code,404)
        diagnostic=self.lab.args.pilot.parent/'finetune-repeatability-001';diagnostic.mkdir()
        (diagnostic/'operator.private.pem').write_text('TEST-ONLY private key fixture')
        self.assertEqual(self.client.get('/evidence/repeatability/operator.private.pem').status_code,404)

    def test_disconnected_model_does_not_fabricate_response(self):
        response=self.client.post('/api/generate',headers=self.headers,json={'model_id':'openai','case_id':'svb-march-2023'})
        self.assertEqual(response.status_code,409)
        self.assertFalse(self.lab.response_path('openai','svb-march-2023').exists())

    def test_fixed_study_http_flow_is_persistent_and_private(self):
        self.assertEqual(self.client.get('/api/study/export').status_code,403)
        preview=self.client.get('/api/study/preview?condition=C').json()
        self.assertIn('advice',preview)
        self.assertEqual(self.lab.study.export(),[])
        self.assertEqual(self.client.post('/api/study/start',json={'consent':True}).status_code,403)
        session=self.client.post('/api/study/start',headers=self.headers,json={'consent':True}).json()
        current=self.client.post('/api/study/current',headers=self.headers,json=session).json()
        self.assertNotIn('candidate_quality',current)
        response={'decision':current['options'][0]['id'],'trust':3,'confidence':4,'difficulty':5,'reliance':'check'}
        body={**session,'ordinal':0,'response':response}
        self.assertEqual(self.client.post('/api/study/submit',headers=self.headers,json={**body,'response':{**response,'trust':8}}).status_code,422)
        self.assertEqual(self.client.post('/api/study/submit',headers=self.headers,json=body).status_code,200)
        self.assertTrue(self.client.post('/api/study/submit',headers=self.headers,json=body).json()['duplicate'])
        exported=self.client.get('/api/study/export',headers=self.headers).json()
        self.assertEqual(sum(row['response'] is not None for row in exported['rows']),1)
        self.assertNotIn(session['session_token'],json.dumps(exported))

    def fixture_response(self):
        (self.lab.args.pilot/'ollama-models.json').write_text(json.dumps({'models':[{'id':'hypertrix','name':'test-only','provider':'ollama'}]}))
        (self.lab.args.pilot/'verification.json').write_text('{"passed":true}')
        path=self.lab.response_path('hypertrix','svb-march-2023');path.parent.mkdir()
        path.write_text(json.dumps({'id':'fixture','model_id':'hypertrix','case_id':'svb-march-2023','answer':'TEST FIXTURE — never live data','answer_sha256':'frozen-fixture-hash'}))

    def test_controlled_conditions_keep_exact_answer(self):
        self.fixture_response()
        for condition in ['A','B','C']:
            def choose(values):return condition if values==['A','B','C'] else values[0]
            with patch.object(self.lab.secrets,'choice',side_effect=choose):
                r=self.client.post('/api/trial',headers=self.headers,json={'case_id':'svb-march-2023','mode':'controlled'}).json()
            self.assertEqual(r['answer'],'TEST FIXTURE — never live data')
            self.assertEqual(r['answer_sha256'],'frozen-fixture-hash');self.assertEqual(r['condition'],condition)
            self.assertNotIn('model_id',r)
            if condition!='C':self.assertNotIn('links',r['disclosure'])

    def test_saved_answer_read_and_verification_gate(self):
        self.fixture_response()
        response=self.client.get('/api/response/hypertrix/svb-march-2023')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['answer_sha256'],'frozen-fixture-hash')
        self.assertEqual(self.client.get('/api/response/hypertrix/unknown').status_code,404)
        (self.lab.args.pilot/'verification.json').unlink()
        trial=self.client.post('/api/trial',headers=self.headers,json={'case_id':'svb-march-2023','mode':'controlled'})
        self.assertEqual(trial.status_code,409)
        self.assertFalse((self.lab.args.root/'ratings.jsonl').exists())

    def test_participant_status_does_not_claim_an_unfinished_replay(self):
        self.fixture_response()
        (self.lab.args.pilot/'replay-verification.json').write_text('{"status":"budget-incomplete"}')
        p=self.client.get('/api/provenance/hypertrix').json()['participant']
        replay=next(s for s in p['steps'] if s['title']=='Repeat the work')
        self.assertEqual(replay['status'],'Replay incomplete')
        self.assertNotEqual(replay['kind'],'checked')

    def test_reveal_requires_saved_rating_and_keeps_frozen_response(self):
        self.fixture_response()
        trial=self.client.post('/api/trial',headers=self.headers,json={'case_id':'svb-march-2023'}).json()
        self.assertNotIn('disclosure',trial)
        body={'session_id':trial['session_id'],'trust':4,'confidence':4,'reliance':'check','decision':'TEST ONLY',
              'responsibility':'shared','phase':'before'}
        r=self.client.post('/api/rating',headers=self.headers,json=body)
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['phase'],'after')
        self.assertEqual(self.client.post('/api/rating',headers=self.headers,json=body).status_code,409)
        body['phase']='after';self.assertEqual(self.client.post('/api/rating',headers=self.headers,json=body).json()['phase'],'complete')
        self.assertEqual(self.client.get('/api/research-summary').json()['paired_reveal_trials'],1)


if __name__=='__main__':unittest.main()
