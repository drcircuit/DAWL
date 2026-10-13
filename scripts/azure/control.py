"""Explicitly scoped Azure pilot lifecycle. Preflight is read-only; deploy is opt-in."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
from prices import estimate

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO/'artifacts/azure-tools/Scripts/python.exe'


def az(*args):
    prefix = [str(TOOLS), '-m', 'azure.cli'] if TOOLS.exists() else ['az']
    result = subprocess.run([*prefix, *map(str, args), '--only-show-errors', '-o', 'json'],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['preflight', 'deploy', 'status', 'stop', 'delete'])
    p.add_argument('--subscription', required=True)
    p.add_argument('--region', default='westeurope')
    p.add_argument('--root', type=Path, default=REPO/'artifacts/azure-pilot-001')
    p.add_argument('--resource-group', default='dawl-pilot-001')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    account = az('account', 'show', '--subscription', a.subscription)
    if account['id'].lower() != a.subscription.lower() or account['state'] != 'Enabled':
        raise ValueError('the exact enabled subscription is required')
    if a.command == 'preflight':
        az('account', 'set', '--subscription', a.subscription)
        quote = estimate(a.region, workers=20, hours=4)
        usage = az('vm', 'list-usage', '--location', a.region, '--subscription', a.subscription)
        skus = az('vm', 'list-skus', '--location', a.region, '--size', 'Standard_D', '--all', '--subscription', a.subscription)
        skus = [s for s in skus if s['name'] in ('Standard_D4as_v5', 'Standard_D8as_v5')]
        user = az('ad', 'signed-in-user', 'show', '--query', 'id')
        with urlopen('https://api.ipify.org', timeout=20) as response:
            address = response.read().decode().strip()
        import ipaddress
        ipaddress.IPv4Address(address)
        report = dict(subscription=a.subscription, region=a.region, quote=quote, skus=skus,
                      usage=usage, admin_object_id=user, operator_cidr=address+'/32')
        save(a.root/'preflight.json', report)
        print(json.dumps(dict(estimate_eur=quote['estimated_envelope_eur'], required_vcpus=72,
                              preflight=str(a.root/'preflight.json')), indent=2))
        return
    if a.command == 'deploy':
        if not a.apply:
            raise ValueError('deploy requires --apply after reviewing preflight and estimate')
        check = json.loads((a.root/'preflight.json').read_text())
        if check['subscription'] != a.subscription or check['region'] != a.region:
            raise ValueError('preflight scope mismatch')
        age = datetime.now(timezone.utc)-datetime.fromisoformat(check['quote']['fetched_at'])
        if age > timedelta(hours=2) or check['quote']['estimated_envelope_eur'] > 50:
            raise ValueError('refresh quote; current approved envelope is EUR 50')
        if len(check['skus']) != 2 or any(s.get('restrictions') for s in check['skus']):
            raise ValueError('review regional SKU restrictions before deployment')
        usage = {v['name']['value'].lower():v for v in check['usage']}
        for name in ('cores', 'standarddasv5family'):
            u = usage.get(name)
            if not u or u['limit']-u['currentValue'] < 72:
                raise ValueError(f'insufficient/unverified quota: {name}; need 72 spare vCPUs')
        if az('group', 'exists', '--name', a.resource_group, '--subscription', a.subscription):
            raise ValueError('pilot requires a new dedicated resource group; refusing to adopt existing resources')
        now = datetime.now(timezone.utc)
        expiry = (now+timedelta(hours=3, minutes=45)).strftime('%Y-%m-%dT%H:%M:%SZ')
        run = dict(subscription=a.subscription, resource_group=a.resource_group, region=a.region,
                   cluster='dawl-pilot', registry='dawlpilot'+now.strftime('%m%d%H%M%S'),
                   started_at=now.isoformat(), expires_at=expiry, budget_eur=50, workers=20)
        save(a.root/'deployment.json', run)  # recovery scope recorded before any mutation
        az('group', 'create', '--name', a.resource_group, '--location', a.region, '--subscription', a.subscription,
           '--tags', 'project=dawl', 'purpose=four-hour-pilot', 'expiresAt='+expiry)
        parameters = {k:dict(value=v) for k,v in dict(registryName=run['registry'], adminObjectId=check['admin_object_id'],
                        operatorCidr=check['operator_cidr'], expiresAt=expiry).items()}
        save(a.root/'parameters.json', {'parameters':parameters})
        try:
            validation = az('deployment', 'group', 'validate', '--subscription', a.subscription,
                            '--resource-group', a.resource_group, '--template-file', REPO/'deploy/azure/main.bicep',
                            '--parameters', '@'+str(a.root/'parameters.json'))
            save(a.root/'arm-validation.json', validation)
            result = az('deployment', 'group', 'create', '--subscription', a.subscription, '--resource-group', a.resource_group,
                        '--name', 'dawl-pilot', '--template-file', REPO/'deploy/azure/main.bicep',
                        '--parameters', '@'+str(a.root/'parameters.json'))
            save(a.root/'arm-result.json', result)
        except Exception:
            # No DAWL keys exist until the later launch step. Roll back only the
            # new, dedicated group this invocation created; preserve local diagnostics.
            try:
                az('group', 'delete', '--subscription', a.subscription, '--name', a.resource_group, '--yes')
            except Exception as failure:
                print('Provisioning rollback failed; inspect dedicated resource group immediately: '+str(failure), file=sys.stderr)
            raise
        print(json.dumps(run, indent=2))
        return
    run = json.loads((a.root/'deployment.json').read_text())
    if run['subscription'] != a.subscription or run['resource_group'] != a.resource_group:
        raise ValueError('saved deployment scope mismatch')
    group = az('group', 'show', '--subscription', a.subscription, '--name', a.resource_group)
    if group.get('tags', {}).get('purpose') != 'four-hour-pilot' or group.get('tags', {}).get('project') != 'dawl':
        raise ValueError('resource group tags do not match this pilot')
    if a.command == 'status':
        cluster = az('aks', 'show', '--subscription', a.subscription, '-g', a.resource_group, '-n', run['cluster'])
        print(json.dumps({k:cluster.get(k) for k in ('powerState', 'provisioningState', 'nodeResourceGroup')}, indent=2))
    elif a.command == 'stop':
        if not a.apply: raise ValueError('stop requires --apply')
        az('aks', 'stop', '--subscription', a.subscription, '-g', a.resource_group, '-n', run['cluster'])
        print('Cluster stop completed. Disks/registry/network remain billable until cleanup.')
    else:
        if not a.apply: raise ValueError('delete requires --apply after exporting evidence and deciding whether to retain signing state')
        az('group', 'delete', '--subscription', a.subscription, '--name', a.resource_group, '--yes')
        print('Dedicated pilot resource group deleted, including its ephemeral signing state.')


if __name__ == '__main__': main()
