"""CPU-only evidence node and consumer commands; no model download required."""
import argparse
import json
from pathlib import Path

import httpx

from dawl_node.common import keygen, load_key, write_json
from dawl_lab.mesh import configure_policy
from .index import Blueprint, Index, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    index = sub.add_parser('index')
    serve = sub.add_parser('serve')
    collect = sub.add_parser('collect')
    verify = sub.add_parser('verify')
    for p in (index, serve, collect, verify):
        p.add_argument('--dataset', type=Path, required=True)
        p.add_argument('--blueprint', required=True, help='Blueprint SHA-256 from a trusted model package')
    for p in (index, serve):
        p.add_argument('--index', type=Path, required=True)
        p.add_argument('--corpus', type=Path, required=True)
    index.add_argument('--splits', default='train')
    serve.add_argument('--identity', type=Path, required=True)
    serve.add_argument('--bind', default='127.0.0.1')
    collect.add_argument('--identity', type=Path, required=True)
    collect.add_argument('--network', type=Path, required=True)
    collect.add_argument('--network-id', required=True)
    collect.add_argument('--discovery', required=True, help='Admitted lab discovery endpoint on port 8913')
    collect.add_argument('--admit', action='append', required=True, help='Allowed evidence-provider Ed25519 public key')
    collect.add_argument('--answer', type=Path, required=True, help='JSON with text, checkpoint and claims [{start,end}]')
    collect.add_argument('--output', type=Path, required=True)
    verify.add_argument('--receipt', type=Path, required=True)
    verify.add_argument('--consumer', required=True)
    verify.add_argument('--answer', type=Path, required=True)
    verify.add_argument('--corpus', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'index':
        result = build(args.index, args.dataset, args.corpus, args.blueprint, tuple(args.splits.split(',')))
    elif args.action == 'verify':
        from .consumer import verify_collection
        from .index import source_bytes
        result = verify_collection(json.loads(args.receipt.read_bytes()), args.consumer,
                                   Blueprint(args.dataset, args.blueprint), json.loads(args.answer.read_bytes())['text'],
                                   lambda digest: source_bytes(args.corpus, digest))
    else:
        args.identity.mkdir(parents=True, exist_ok=True)
        private = args.identity / 'key.private.pem'
        if not private.exists():
            keygen(private, args.identity / 'key.public.hex')
        key = load_key(private)
        if args.action == 'serve':
            import uvicorn
            from .provider import app
            api = app(Index(args.index, args.dataset, args.corpus, args.blueprint), key)
            uvicorn.run(api, host=args.bind, port=8914, access_log=False, limit_concurrency=32)
            return
        from .consumer import collect as collect_evidence, discover
        policy = json.loads(args.network.read_bytes())
        configure_policy(policy, args.network_id)
        bp = Blueprint(args.dataset, args.blueprint)
        answer = json.loads(args.answer.read_bytes())
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            providers = discover(client, args.discovery, args.network_id, args.blueprint, set(args.admit))
            result = collect_evidence(answer['text'], answer['claims'], answer['checkpoint'], bp, providers, key, client)
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
