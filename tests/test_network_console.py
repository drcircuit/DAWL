import copy
from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from network_metrics import aggregate,compute_report
from network_view import view,public_network,clean


class NetworkAccountingTests(unittest.TestCase):
    def sample(self,seconds,cpu,rx,flops):
        timestamp=f'2026-10-14T18:00:{seconds:02d}+00:00'
        return {'observed_at':timestamp,'nodes':[{'id':'a','running':True,'stats':{'cpu_time':f'{cpu}s','mem_usage':'1GB / 4GB'},
                'compute':{'schema':'hypertrix.node-compute/1','observed_at':timestamp,'counter_epoch':'first','kind':'estimated','flops_total':flops,'convention':'scalar-fp-ops-fma2'}}],
                'network_namespaces':{'shared':{'rx_bytes':rx,'tx_bytes':rx*2}}}

    def test_counter_rates_reset_and_missing_are_distinct(self):
        old=self.sample(0,10,1000,100);new=self.sample(10,30,5000,1100)
        a=aggregate(new,old)
        self.assertEqual((a['cpu_cores'],a['rx_bytes_per_second'],a['tx_bytes_per_second'],a['flops_per_second']),(2,400,800,100))
        self.assertEqual(a['network_namespaces'],1);self.assertEqual(a['compute_kind'],'estimated')
        self.assertIsNone(aggregate(new)['flops_per_second'])
        reset=self.sample(10,1,5,10);reset['nodes'][0]['compute']['counter_epoch']='restart'
        r=aggregate(reset,old)
        self.assertIsNone(r['flops_per_second']);self.assertIsNone(r['rx_bytes_per_second']);self.assertIsNone(r['cpu_cores'])
        self.assertIsNone(compute_report({**new['nodes'][0]['compute'],'flops_total':float('nan')},datetime(2026,10,14,18,0,10,tzinfo=timezone.utc)))

    def test_stale_observation_is_not_online_or_current_metrics(self):
        p={'certified':True,'state':{'height':3},'telemetry':self.sample(0,10,1000,100)}
        p['telemetry']['nodes'][0].update(role='trainer',name='trainer-0');p['telemetry']['aggregate']={'cpu_cores':2}
        v=view(p,datetime(2026,10,14,18,1,tzinfo=timezone.utc))
        self.assertFalse(v['fresh']);self.assertFalse(v['nodes'][0]['running']);self.assertEqual(v['metrics'],{})

    def test_public_roster_contains_membership_not_liveness_or_endpoints(self):
        state={'providers':{'key':{'endpoint':'http://private:8900','name':'host-secret'}},'training_members':{},'plan':{'actors':{}}}
        n=public_network(state,{'validators':[]})['nodes'][0]
        self.assertEqual(n['status'],'Registered');self.assertNotIn('running',n);self.assertNotIn('endpoint',n);self.assertNotIn('host-secret',str(n))

    def test_terminal_control_sequences_are_removed(self):
        self.assertNotIn('\x1b',clean('\x1b]0;title\x07'))

    def test_stale_work_drops_rate_even_when_main_telemetry_is_fresh(self):
        p={'certified':True,'state':{},'telemetry':self.sample(10,10,1000,100)}
        p['telemetry']['nodes'][0].update(role='model-trainer',name='hypertrix-seed',activity={
            'observed_at':'2026-10-14T17:59:00+00:00','job':'Seed II','live':True,'status':'running','phase':'Training weights','targets_per_second':999})
        v=view(p,datetime(2026,10,14,18,0,10,tzinfo=timezone.utc))
        self.assertFalse(v['activities'][0]['live']);self.assertNotIn('targets_per_second',v['activities'][0])


if __name__=='__main__':unittest.main()
