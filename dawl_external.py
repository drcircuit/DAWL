"""Prepare a fresh external DAWL network without sharing operator private keys."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent/"src"))
from dawl_node.common import canonical_json
from dawl_cluster.external import identity, assemble, install, seed_provider


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    local=commands.add_parser("identity")
    local.add_argument("--root",type=Path,required=True)
    local.add_argument("--name",required=True)
    local.add_argument("--role",choices=("validator","provider","worker","publisher"),required=True)
    local.add_argument("--host",required=True,help="IPv4 address on a preconfigured private mesh")
    local.add_argument("--transport",choices=("ssh-loopback/1",),help="explicit authenticated tunnel profile")
    network=commands.add_parser("assemble")
    network.add_argument("--root",type=Path,required=True)
    network.add_argument("--template",type=Path,required=True,help="existing provider with pinned plan and public lineage")
    network.add_argument("--registrations",type=Path,nargs="+",required=True)
    network.add_argument("--chain-id",required=True)
    network.add_argument("--image",required=True,help="image@sha256:registry-manifest-digest")
    network.add_argument("--role-images",type=Path,help="JSON mapping of all four node roles to immutable image pins")
    node=commands.add_parser("install")
    node.add_argument("--root",type=Path,required=True)
    node.add_argument("--bundle",type=Path,required=True)
    node.add_argument("--network-sha256",required=True,help="independently agreed network manifest pin")
    node.add_argument("--bind-host",help="explicit 0.0.0.0 bind behind a private Kubernetes Service")
    seed=commands.add_parser("seed-provider")
    seed.add_argument("--root",type=Path,required=True)
    seed.add_argument("--objects",type=Path,required=True)
    args=vars(parser.parse_args());command=args.pop("command")
    if command=="install": args["pin"]=args.pop("network_sha256")
    if command=="assemble" and args.get("role_images"):
        import json
        args["role_images"]=json.loads(args["role_images"].read_text())
    result={"identity":identity,"assemble":assemble,"install":install,"seed-provider":seed_provider}[command](**args)
    print(canonical_json(result).decode())


if __name__=="__main__": main()
