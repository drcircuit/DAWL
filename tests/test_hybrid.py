"""Tunnel identities are opt-in and cannot expose ABCI or wildcard listeners."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hybrid_topology import members, endpoints
from test_external import BootstrapTests
from dawl_cluster.external import identity, install, validate_registration
from dawl_node.common import read_json
from node_topology import default_topology,validate
from dawl_cluster.external import assemble
import copy


class TunnelTests(BootstrapTests):
    def test_loopback_requires_explicit_signed_transport(self):
        node=self.root/'tunnel'
        with self.assertRaises(ValueError): identity(node,'tunnel','worker','127.70.0.99')
        for host in ('127.0.0.1','127.70.0.0','127.70.0.255','8.8.8.8','10.0.0.1'):
            with self.assertRaises(ValueError): identity(node,'tunnel','worker',host,transport='ssh-loopback/1')
        identity(node,'tunnel','worker','127.70.0.99',transport='ssh-loopback/1')
        reg=read_json(node/'registration.json')
        self.assertEqual(validate_registration(reg)['transport'],'ssh-loopback/1')
        reg['registration']['body']['transport']='private'
        with self.assertRaises(ValueError): validate_registration(reg)

    def test_tunnel_bundle_has_per_validator_private_abci(self):
        self.nodes=[]
        for i,role in enumerate(['validator']*4+['provider','worker','worker','publisher']):
            node=self.root/f'tunnel-{i}'
            identity(node,f'tunnel-{i}',role,f'127.70.0.{10+i}',transport='ssh-loopback/1')
            self.nodes.append(node)
        result=self.assemble()
        for i,node in enumerate(self.nodes[:4]):
            install(node,self.bundle,result['network_sha256'])
            config=read_json(node/'cluster.json')
            self.assertEqual(config['abci_host'],f'127.70.0.{10+i}')
            self.assertIn(f'proxy_app = "tcp://127.70.0.{10+i}:26658"',(node/'comet/config/config.toml').read_text())
        with self.assertRaises(ValueError): install(self.nodes[4],self.bundle,result['network_sha256'],bind_host='0.0.0.0')

    def test_mixed_transport_membership_rejected(self):
        node=self.root/'tunnel'
        identity(node,'tunnel','worker','127.70.0.99',transport='ssh-loopback/1')
        self.nodes.append(node)
        with self.assertRaisesRegex(ValueError,'one transport'): self.assemble()

    def test_role_images_pin_each_role_and_require_private_unix_adapter(self):
        self.nodes=[]
        roles=['validator']*4+['provider','worker','worker','publisher']
        for i,role in enumerate(roles):
            node=self.root/f'isolated-{i}'
            identity(node,f'isolated-{i}',role,f'127.70.0.{10+i}',transport='ssh-loopback/1')
            self.nodes.append(node)
        pins={r:'registry/'+r+'@sha256:'+str(i)*64 for i,r in enumerate(sorted(set(roles)))}
        second=self.root/'role-network'
        result=assemble(second,self.template,[p/'registration.json' for p in self.nodes],
                        'role-isolated-chain','registry/base@sha256:'+'a'*64,role_images=pins)
        install(self.nodes[0],second,result['network_sha256'])
        node=self.nodes[0]
        self.assertTrue(read_json(node/'cluster.json')['abci_unix'])
        self.assertIn('proxy_app = "unix://',(node/'comet/config/config.toml').read_text())
        self.assertEqual(read_json(node/'compose.json')['services']['node']['image'],pins['validator'])


class TopologyTests(unittest.TestCase):
    def test_two_sites_and_only_required_forwarded_ports(self):
        nodes=members()
        self.assertEqual(len({n['host'] for n in nodes}),27)
        self.assertEqual(sum(n['role']=='worker' for n in nodes),20)
        for site in ('cloud','local'):
            self.assertEqual(sum(n['role']=='validator' and n['site']==site for n in nodes),2)
            self.assertEqual(len(endpoints(site)),5)
            self.assertFalse(any(port in (26658,26659) for _,port in endpoints(site)))

    def test_hypertrix_resource_budget_and_unique_node_assignments(self):
        topology=validate(default_topology())
        self.assertEqual(sum(n['site']=='cloud' for n in topology['nodes']),7)
        self.assertEqual(len({n['name'] for n in topology['nodes']}),27)
        self.assertEqual(sum(n['name'].startswith('daemon-') for n in topology['nodes']),20)
        bad=copy.deepcopy(topology);bad['nodes'][0]['cpu']=1
        with self.assertRaises(ValueError): validate(bad)
        bad=copy.deepcopy(topology);bad['nodes'][0]['host']='0.0.0.0'
        with self.assertRaises(ValueError): validate(bad)
        bad=copy.deepcopy(topology);bad['nodes'][0]['memory_gb']=.75
        with self.assertRaisesRegex(ValueError,'increments of 0.1'): validate(bad)


if __name__=='__main__': unittest.main()
