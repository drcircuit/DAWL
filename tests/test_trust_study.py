"""Study fixtures stay in temporary directories, never the live participant store."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from trust_study import Study, documentation


class FixedStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.args=(self.root,ROOT/'research/trust-study-v2.json',[ROOT/'scripts/trust_study.py'],self.root/'absent')
        self.study=Study(*self.args)
    def tearDown(self):self.tmp.cleanup()
    def test_identical_advice_and_no_rubric_or_quality_in_participant_payload(self):
        for case in self.study.cases:
            for variant in ('good','bad'):
                views=[self.study.payload(case,variant,c) for c in 'ABC']
                self.assertEqual(len({p['advice_sha256'] for p in views}),1)
                self.assertEqual(len({p['scenario_sha256'] for p in views}),1)
                for p in views:
                    self.assertFalse({'advice_variants','condition','candidate_quality','provisional_answer','intended_error'} & set(p))
        b=documentation('B');c=documentation('C')
        for s in c['steps']:s.pop('evidence')
        self.assertEqual(b,c)
    def test_complete_block_balances_condition_and_each_quality(self):
        for _ in range(24):self.study.start()
        rows=self.study.export()
        for condition in 'ABC':
            for case in self.study.cases:
                for quality in ('good','bad'):
                    self.assertEqual(sum(r['condition']==condition and r['case_id']==case and r['candidate_quality']==quality for r in rows),4)
    def test_restart_identity_retry_and_order(self):
        s=self.study.start();sid,token=s['session_id'],s['session_token']
        p=self.study.current(sid,token);restarted=Study(*self.args)
        self.assertEqual(restarted.current(sid,token),p)
        with self.assertRaises(PermissionError):restarted.current(sid,'wrong')
        response={'decision':p['options'][0]['id'],'trust':3,'confidence':5,'difficulty':4,'reliance':'check','reason':'test fixture'}
        with self.assertRaises(ValueError):restarted.submit(sid,token,1,response)
        restarted.submit(sid,token,0,response)
        self.assertTrue(restarted.submit(sid,token,0,response)['duplicate'])
        with self.assertRaises(ValueError):restarted.submit(sid,token,0,{**response,'trust':4})
        self.assertEqual(restarted.current(sid,token)['ordinal'],1)
        self.assertEqual(sum(r['response'] is not None for r in restarted.export()),1)
    def test_preview_does_not_allocate_and_evidence_is_frozen(self):
        for c in 'ABC':self.study.payload('extra-payment','good',c)
        self.assertEqual(self.study.export(),[])
        e=self.root/'source';e.mkdir();(e/'base-lock.json').write_text('{"source":1}')
        s=Study(self.root/'frozen',self.args[1],self.args[2],e)
        path=s.root/'evidence'/s.evidence['base-lock.json']
        (e/'base-lock.json').write_text('{"source":2}')
        self.assertEqual(json.loads(path.read_text()),{'source':1})
    def test_mechanical_arithmetic_only(self):
        from decimal import Decimal as D
        self.assertEqual(20000*D('.18')-20000*D('.07'),2200)
        self.assertEqual(D('.20')+D('.35'),D('.55'))
        self.assertEqual(D('.40')+D('.05'),D('.45'))
        self.assertEqual(48*2500+2000-24*4500,14000)


if __name__=='__main__':unittest.main()
