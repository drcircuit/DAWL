"""Inspect or submit owner recovery decisions using a node's certified RPC client."""
import argparse
import asyncio
import json
from pathlib import Path
from dawl_cluster.client import Client
from dawl_cluster.training_recovery import enabled
from dawl_node.common import load_key


async def run(args):
    key = None if args.action == 'status' else load_key(args.root/'key.private.pem')
    client = Client(args.root, key)
    try:
        if not client.certified: raise ValueError('Recovery requires quorum-certified state reads')
        state = await client.state(); stage = state['training_stages'][args.stage]
        if not enabled(stage): raise ValueError('Stage does not opt in to recovery')
        if args.action == 'status':
            print(json.dumps({'stage': stage['id'], 'status': stage['status'], 'round': stage['round'],
                'revision': stage['recovery_revision'], 'head': stage['head'], 'height': state['height'],
                'deadline_height': stage['round_started_height']+stage['body']['recovery']['round_timeout_blocks'],
                'failure_reports': stage['failure_reports'], 'recovery_history': stage['recovery_history']}, indent=2))
            return
        payload = {'stage': stage['id'], 'round': stage['round'], 'revision': stage['recovery_revision']}
        if args.action == 'reject':
            if not args.report: raise ValueError('A signed failure report ID is required')
            payload['report'] = args.report; command = 'reject_training_update'
        else:
            payload['reason'] = 'disputed' if stage['status'] == 'disputed' else 'round_timeout'
            command = 'cancel_training_stage'
        print(json.dumps(await client.command(command, payload), indent=2))
    finally:
        await client.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['status', 'reject', 'cancel'])
    p.add_argument('--root', type=Path, required=True); p.add_argument('--stage', required=True)
    p.add_argument('--report')
    asyncio.run(run(p.parse_args()))


if __name__ == '__main__': main()
