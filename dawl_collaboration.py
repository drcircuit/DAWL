"""Participation manifests, execution cohorts and explicit model selection."""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
from dawl_node.common import canonical_json, read_json, write_json, load_key
from dawl_cluster.collaboration import identity, EXACT, select_checkpoint, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['fingerprint', 'manifest', 'seal', 'activate', 'register', 'fork', 'stage', 'join', 'status', 'select'])
    p.add_argument('--root', type=Path, help='Existing node directory with pinned genesis, plan and cluster.json')
    p.add_argument('--file', type=Path, help='JSON input: manifest, branch, stage or consumer selection')
    p.add_argument('--output', type=Path, help='Write an immutable JSON result')
    p.add_argument('--mode', choices=['homogeneous', 'heterogeneous'], default='homogeneous')
    p.add_argument('--name', default='Research collaboration')
    p.add_argument('--environments', nargs='+', type=Path)
    p.add_argument('--members', type=Path, help='JSON array of already admitted trainer/verifier public keys')
    p.add_argument('--profiles', nargs='+')
    p.add_argument('--device', default='cuda:0')
    args = p.parse_args()

    async def run():
        if args.command == 'fingerprint':
            from dawl_training.environment import capture
            return capture(args.device)
        if args.command == 'seal':
            if not args.file: p.error('seal requires --file containing a body')
            body = read_json(args.file); return {'id': identity(body), 'body': body}
        if args.command == 'manifest':
            if not args.environments or not args.members or not args.profiles:
                p.error('manifest requires --environments, --members and --profiles')
            from dawl_cluster.collaboration import validate_environment
            environments = sorted((read_json(f) for f in args.environments), key=lambda e: e['id'])
            for environment in environments: validate_environment(environment)
            if len({e['id'] for e in environments}) != len(environments) or len(environments) > 8:
                p.error('use 1..8 distinct environments')
            if args.mode == 'homogeneous' and len(environments) != 1:
                p.error('homogeneous mode requires exactly one environment')
            body = {'schema': 'hypertrix.collaboration-manifest/1', 'name': args.name, 'mode': args.mode,
                    'profiles': sorted(set(args.profiles)), 'members': sorted(set(read_json(args.members))),
                    'environments': environments, 'verification': EXACT, 'admission': 'explicit-members/1'}
            return {'id': identity(body), 'body': body}
        if not args.root: p.error('this command requires --root')
        if args.command in ('register', 'fork', 'stage', 'join', 'select') and not args.file:
            p.error('this command requires --file')
        from dawl_cluster.client import Client
        client = Client(args.root, None if args.command in ('status', 'select') else load_key(args.root/'key.private.pem'))
        if not client.certified: raise ValueError('collaboration commands require verify_state=true and pinned genesis')
        try:
            if args.command == 'status': return summary(await client.state())
            if args.command == 'select': return select_checkpoint(await client.state(), read_json(args.file))
            if args.command == 'activate':
                from dawl_platform.workflow import implementation_digest
                return await client.command('activate_collaboration', {'version': 5, 'implementation_sha256': implementation_digest()})
            if args.command == 'join':
                from dawl_training.environment import capture
                from dawl_swarm.dht import atomic
                value = read_json(args.file); state = await client.state(); registered = state['collaborations'][value['id']]
                if value['body'] != registered['body']: raise ValueError('local manifest differs from certified manifest')
                measured = capture(args.device)
                if measured not in value['body']['environments']: raise ValueError('measured environment not allowed by manifest')
                payload = {'collaboration': value['id'], 'environment': measured['id']}
                accepted = registered['acceptances'].get(client.public)
                if accepted:
                    if any(accepted[k] != v for k, v in payload.items()): raise ValueError('identity previously accepted another cohort')
                    result = {'accepted': True, **payload}
                else: result = await client.command('accept_collaboration', payload)
                atomic(args.root/'cluster.json', {**client.config, 'collaboration_manifest': value['id'],
                                                 'execution_environment': measured['id']})
                return result
            kind, field = {'register': ('register_collaboration', 'manifest'), 'fork': ('register_model_branch', 'branch'),
                           'stage': ('register_training_stage', 'stage')}[args.command]
            return await client.command(kind, {field: read_json(args.file)})
        finally: await client.close()
    result = asyncio.run(run())
    if args.output: write_json(args.output, result)
    else: print(canonical_json(result).decode())


if __name__ == '__main__': main()
