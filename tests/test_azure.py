"""Cloud packaging safety and identity preservation; no billable operations."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'azure'))
from manifests import manifests, members
from prices import compute_price, estimate
import control
from test_external import BootstrapTests
from dawl_cluster.external import identity, install
from dawl_node.common import read_json


class KubernetesTests(unittest.TestCase):
    def test_twenty_and_forty_have_unique_services_and_single_identity_per_disk(self):
        for workers in (20, 40):
            docs = manifests(workers, 'registry/dawl@sha256:'+'a'*64)['items']
            services = [d for d in docs if d['kind'] == 'Service']
            self.assertEqual(len(services), workers+7)
            self.assertEqual(len({d['spec']['clusterIP'] for d in services}), workers+7)
            self.assertTrue(all(d['spec']['type'] == 'ClusterIP' for d in services))
            for d in (d for d in docs if d['kind'] == 'StatefulSet'):
                self.assertEqual(d['spec']['replicas'], 1)
                pod = d['spec']['template']['spec']
                self.assertFalse(pod['automountServiceAccountToken'])
                self.assertNotIn('hostNetwork', pod)
                self.assertTrue(d['spec']['volumeClaimTemplates'])
                if d['metadata']['name'].startswith('validator'):
                    self.assertIn('requiredDuringSchedulingIgnoredDuringExecution', pod['affinity']['podAntiAffinity'])

    def test_no_unbounded_scale_or_mutable_image(self):
        with self.assertRaises(ValueError): members(100)
        with self.assertRaises(ValueError): manifests(20, 'registry/dawl:latest')

    def test_price_ignores_windows_spot_and_cloud_services(self):
        base = dict(productName='Virtual Machines Dasv5 Series', meterName='D4as v5', unitOfMeasure='1 Hour', retailPrice=.2)
        rows = [base, {**base, 'productName':'Dasv5 Series Cloud Services', 'retailPrice':.9},
                {**base, 'productName':'Virtual Machines Dasv5 Series Windows', 'retailPrice':.8},
                {**base, 'meterName':'D4as v5 Spot', 'retailPrice':.1}]
        with patch('prices.query', return_value=rows):
            self.assertEqual(compute_price('westeurope', 'Standard_D4as_v5'), base)
        with patch('prices.compute_price', return_value=base):
            result = estimate(workers=20)
        self.assertEqual(result['node_count'], 11)  # three 2-CPU workers per 8-vCPU host, reserve OS capacity
        self.assertGreater(result['estimated_envelope_eur'], result['compute_eur'])


class CloudBootstrapTests(BootstrapTests):
    def test_forty_workers_assemble_without_changing_quorum(self):
        for i in range(38):
            node = self.root/f'additional-{i}'
            identity(node, f'additional-{i}', 'worker', f'10.55.1.{10+i}')
            self.nodes.append(node)
        result = self.assemble()
        self.assertEqual(result['members'], 46)
        self.assertEqual(len(read_json(self.bundle/'genesis.json')['validators']), 4)

    def test_service_advertisement_survives_wildcard_listening(self):
        result = self.assemble()
        install(self.nodes[0], self.bundle, result['network_sha256'], bind_host='0.0.0.0')
        config = (self.nodes[0]/'comet/config/config.toml').read_text()
        self.assertIn('laddr = "tcp://0.0.0.0:26657"', config)
        self.assertIn('external_address = "10.55.0.10:26656"', config)
        self.assertIn('http://10.55.0.10:26657', read_json(self.nodes[0]/'cluster.json')['rpcs'])
        with self.assertRaises(ValueError):
            install(self.nodes[1], self.bundle, result['network_sha256'], bind_host='8.8.8.8')


class SpendingBoundaryTests(unittest.TestCase):
    def run_deployment(self, *, budget=33, quota=72, existing=False, fail=False):
        calls=[]
        def fake_az(*args):
            calls.append(args)
            if args[:2]==('account','show'): return dict(id='approved',state='Enabled')
            if args[:2]==('group','exists'): return existing
            if fail and args[:3]==('deployment','group','create'): raise RuntimeError('simulated provisioning failure')
            return {}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            control.save(root/'preflight.json',dict(subscription='approved',region='westeurope',
                quote=dict(fetched_at=datetime.now(timezone.utc).isoformat(),estimated_envelope_eur=budget),
                skus=[dict(name='Standard_D4as_v5'),dict(name='Standard_D8as_v5')],
                usage=[dict(name=dict(value=n),limit=quota,currentValue=0) for n in ('cores','standarddasv5family')],
                admin_object_id='user',operator_cidr='192.0.2.1/32'))
            with patch.object(sys,'argv',['control.py','deploy','--subscription','approved','--root',str(root),'--apply']), patch('control.az',side_effect=fake_az):
                with self.assertRaises((ValueError,RuntimeError)):
                    control.main()
        return calls

    def test_over_budget_cannot_create_resources(self):
        calls=self.run_deployment(budget=51)
        self.assertFalse(any(c[:2]==('group','create') for c in calls))

    def test_insufficient_quota_cannot_create_resources(self):
        calls=self.run_deployment(quota=16)
        self.assertFalse(any(c[:2]==('group','create') for c in calls))

    def test_existing_group_is_never_adopted_or_deleted(self):
        calls=self.run_deployment(existing=True)
        self.assertFalse(any(c[:2] in [('group','create'),('group','delete')] for c in calls))

    def test_failed_infrastructure_rolls_back_only_its_new_group(self):
        calls=self.run_deployment(fail=True)
        deletes=[c for c in calls if c[:2]==('group','delete')]
        self.assertEqual(deletes,[('group','delete','--subscription','approved','--name','dawl-pilot-001','--yes')])


if __name__ == '__main__': unittest.main()
