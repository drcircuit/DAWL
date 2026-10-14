"""Profile protocol operator CLI. Commands use existing signed, certified clients."""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from dawl_node.common import canonical_json, load_key, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['provider','worker','export','activate','register','contribute','publish','weights','status'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--profile-id')
    parser.add_argument('--file', type=Path, help='Profile, contribution proposal, activation pins or named weight manifest JSON')
    parser.add_argument('--key', type=Path, help='Export signing key; defaults to root/key.private.pem')
    parser.add_argument('--blueprint', help='Local signed Blueprint ID for export')
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=8900)
    parser.add_argument('--allow-insecure-lab', action='store_true'); parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if args.command == 'provider':
        import uvicorn
        from dawl_profiles.provider import app
        import ipaddress
        if not ipaddress.ip_address(args.host).is_loopback and not args.allow_insecure_lab:
            parser.error('public plaintext binding requires explicit --allow-insecure-lab')
        uvicorn.run(app(args.root), host=args.host, port=args.port, access_log=False); return
    if args.command == 'worker':
        from dawl_cluster.profile_worker import run
        asyncio.run(run(args.root, args.once)); return
    if args.command == 'export':
        from dawl_profiles.workspace import Workspace
        from dawl_profiles.bundles import export_bundle, export_index
        from data_ingestion.protocol import require_digest
        require_digest(args.profile_id); require_digest(args.blueprint)
        directory = args.root/'profiles'/args.profile_id
        ws = Workspace(args.root); ledger, tok = ws.open_profile(read_json(directory/'profile.json'), read_json(directory/'trust.json'))
        result = export_bundle(ws, ledger, tok, ledger.get(args.blueprint), load_key(args.key or args.root/'key.private.pem'))
        if not args.file: parser.error('export needs --file for the public contribution proposal')
        write_json(args.file, result)
        path = args.root/'artifact-index.json'; index = read_json(path) if path.exists() else {}
        from dawl_swarm.dht import atomic
        index.update(export_index(ws, [result])); atomic(path, index)
        print(canonical_json(result).decode()); return
    from dawl_cluster.client import Client
    from dawl_cluster.profile_protocol import summary
    async def command():
        client = Client(args.root, None if args.command == 'status' else load_key(args.root/'key.private.pem'))
        try:
            if args.command == 'status': return summary(await client.state())
            if args.command in ('activate','register','contribute','weights') and not args.file:
                parser.error('this command requires --file')
            if args.command == 'activate': kind, payload = 'activate_profiles', read_json(args.file)
            elif args.command == 'register': kind, payload = 'register_profile', {'profile': read_json(args.file)}
            elif args.command == 'contribute': kind, payload = 'contribute_profile', read_json(args.file)
            elif args.command == 'weights': kind, payload = 'register_profile_weights', {'profile_id': args.profile_id, 'manifest': read_json(args.file)}
            else: kind, payload = 'publish_profile', {'profile_id': args.profile_id}
            return await client.command(kind, payload)
        finally: await client.close()
    print(canonical_json(asyncio.run(command())).decode())


if __name__ == '__main__': main()
