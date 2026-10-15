"""Create a shared, hash-pinned private LAN discovery policy."""
import argparse
from pathlib import Path
from dawl_lab.mesh import configure_policy
from dawl_node.common import canonical_json,sha256,immutable_write
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--cidr',action='append',required=True);a=p.parse_args()
policy={'schema':'hypertrix.lab-network/1','allowed_cidrs':sorted(set(a.cidr))}
digest=sha256(canonical_json(policy));configure_policy(policy,digest)
immutable_write(a.output,canonical_json(policy));print('Network ID: '+digest)
