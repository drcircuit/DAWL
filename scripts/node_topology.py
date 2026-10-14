"""Declarative placement and resource budgets for independently packaged nodes."""
from decimal import Decimal
import ipaddress
import re
from hybrid_topology import members


def default_topology():
    nodes=members()
    for node in nodes:
        role=node['role'];cloud=node['site']=='cloud'
        node['name']=('blueprint-publisher' if role=='publisher' else
                      {'validator':'oracle','provider':'cortex','worker':'daemon'}[role]+'-'+node['name'].rsplit('-',1)[-1])
        node['cpu']=.25 if role in ('worker','publisher') and cloud else .5 if role in ('validator','provider') else 1
        node['memory_gb']=1.25 if role=='worker' else .75 if role=='validator' else 1 if role=='provider' else .5
        if cloud: node['memory_gb']={'validator':.8,'provider':1.1,'worker':1.2,'publisher':.5}[role]
    return dict(schema='dawl.container-topology/1',nodes=nodes,gateway=dict(cpu=.5,memory_gb=.5),cloud_vcpus=3)


def validate(topology):
    if topology.get('schema')!='dawl.container-topology/1': raise ValueError('Unsupported topology')
    nodes=topology['nodes']
    if not 8<=len(nodes)<=64: raise ValueError('Topology size outside admitted bounds')
    roles={'validator','provider','publisher','worker'}
    for field in ('name','host'):
        if len({n[field] for n in nodes})!=len(nodes): raise ValueError('Repeated node '+field)
    for node in nodes:
        if set(node)!={'name','host','role','site','cpu','memory_gb'}: raise ValueError('Unexpected node fields')
        if not re.fullmatch('[a-z][a-z0-9-]{0,39}',node['name']) or node['role'] not in roles or node['site'] not in ('local','cloud'):
            raise ValueError('Invalid node assignment')
        address=ipaddress.IPv4Address(node['host'])
        if address not in ipaddress.ip_network('127.70.0.0/24') or str(address).endswith(('.0','.255')): raise ValueError('Invalid tunnel address')
        if not 0<float(node['cpu'])<=3 or not .25<=float(node['memory_gb'])<=8: raise ValueError('Invalid resource request')
        if node['site']=='cloud' and Decimal(str(node['memory_gb']))%Decimal('0.1'):
            raise ValueError('ACI memory requests must use increments of 0.1 GB')
    if sum(n['role']=='validator' for n in nodes)!=4 or sum(n['role']=='publisher' for n in nodes)!=1:
        raise ValueError('Four validators and one publisher required')
    if not any(n['role']=='provider' for n in nodes): raise ValueError('Provider required')
    cpu=sum((Decimal(str(n['cpu'])) for n in nodes if n['site']=='cloud'),Decimal(str(topology['gateway']['cpu'])))
    memory=sum((Decimal(str(n['memory_gb'])) for n in nodes if n['site']=='cloud'),Decimal(str(topology['gateway']['memory_gb'])))
    if 'console' in topology:
        if topology['console']!={'cpu':.2,'memory_gb':.5}:raise ValueError('Unexpected read-only Console allocation')
        cpu+=Decimal(str(topology['console']['cpu']));memory+=Decimal(str(topology['console']['memory_gb']))
    if cpu!=Decimal(3) or topology['cloud_vcpus']!=3 or memory>8: raise ValueError('Cloud pool must remain exactly three vCPUs and at most eight GB')
    return topology
