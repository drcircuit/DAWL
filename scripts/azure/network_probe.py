"""Isolate managed ACI ingress after closing the recorded pilot; never copy signers."""
import base64
import argparse
from datetime import datetime, timezone
import hashlib
import json
import tarfile
import time
from urllib.request import build_opener, ProxyHandler, Request

from azure.storage.fileshare import ShareFileClient
from control import REPO, TOOLS, save
from launch import command


def az(*args):
    # Bound individual management calls as well as the diagnostic workload.
    output = command(TOOLS, '-m', 'azure.cli', *args, '--only-show-errors', '-o', 'json', timeout=60)
    return json.loads(output) if output.strip() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', choices=['minimal', 'nine', 'nine-conservative', 'nine-gateway-last', 'eight-original', 'nine-omit-empty-ports', 'nine-no-limits', 'nine-aux-limits', 'nine-aux-public-ports'], default='minimal')
    args = parser.parse_args()
    root = REPO / 'artifacts/azure-hybrid-001'
    run = json.loads((root / 'deployment.json').read_text())
    assert run['subscription'] == '58eadcc1-71bf-4547-bd13-7f68a6042a0f'
    assert run['resource_group'] == 'DAWL' and run['region'] == 'norwayeast'
    scope = ['--subscription', run['subscription'], '-g', 'DAWL']
    pool = run['prefix'] + '-nodes'
    current = az('container', 'show', *scope, '-n', pool)
    assert current['tags']['pilot'] == run['prefix']
    deadline = json.loads((root / 'contribution-estimate.json').read_text())['shutdown_at']
    assert (datetime.fromisoformat(deadline)-datetime.now(timezone.utc)).total_seconds() > 900
    if current['instanceView']['state'] == 'Stopped':
        # A previous diagnostic already closed and preserved this group.
        evidence = json.loads((root / 'pre-recreate-archives.json').read_text())
        assert len(evidence['archives']) == 7
        for entry in evidence['archives']:
            with open(entry['path'], 'rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
        run_probe(root, run, scope, args.layout)
        return
    active = json.loads((root / 'active-contribution-deployment.json').read_text())['name']
    deployment = az('deployment', 'group', 'show', *scope, '-n', active)
    if deployment['properties']['provisioningState'] in ('Running', 'Accepted', 'Creating'):
        az('deployment', 'group', 'cancel', *scope, '-n', active)
    code = "from pathlib import Path;Path('/exchange/stop').touch();print('CLOSE_REQUESTED')"
    expression = "python -c exec(__import__('base64').b64decode('" + base64.b64encode(code.encode()).decode() + "'))"
    out = command(TOOLS, '-m', 'azure.cli', 'container', 'exec', *scope, '-n', pool,
                  '--container-name', 'gateway', '--exec-command', expression, '--only-show-errors').decode()
    assert 'CLOSE_REQUESTED' in out
    for _ in range(20):
        current = az('container', 'show', *scope, '-n', pool)
        nodes = [c for c in current['containers'] if c['name'] not in ('gateway', 'console')]
        assert len(nodes) == 7
        if all(c['instanceView']['currentState']['state'] == 'Terminated' for c in nodes):
            break
        time.sleep(3)
    else:
        raise RuntimeError('Node archive writers did not close')
    folder = root / ('network-review-private-archives-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    folder.mkdir()
    key = az('storage', 'account', 'keys', 'list', *scope, '--account-name', run['storage'])[0]['value']
    rows = []
    for node in nodes:
        client = ShareFileClient('https://' + run['storage'] + '.file.core.windows.net',
                                share_name='nodes-' + node['name'], file_path='private-state.tar', credential=key)
        path = folder / (node['name'] + '.tar')
        digest = hashlib.sha256()
        with path.open('wb') as output:
            for chunk in client.download_file().chunks():
                output.write(chunk)
                digest.update(chunk)
        with tarfile.open(path) as archive:
            assert {'key.private.pem', 'registration.json', 'installed.json'}.issubset(archive.getnames())
            for member in archive:
                assert member.isfile() and not member.name.startswith('/') and '..' not in member.name.split('/')
                stream = archive.extractfile(member)
                while stream.read(1024*1024):
                    pass
        rows.append(dict(name=node['name'], path=str(path), bytes=path.stat().st_size, sha256=digest.hexdigest()))
    evidence = dict(at=datetime.now(timezone.utc).isoformat(), archives=rows)
    save(folder / 'manifest.json', evidence)
    save(root / 'pre-recreate-archives.json', evidence)
    params = json.loads((root / 'restart-parameters.json').read_text())
    params['parameters']['restoreHashes']['value'] = {r['name']: r['sha256'] for r in rows}
    save(root / 'restart-parameters.json', params)
    az('container', 'stop', *scope, '-n', pool)
    assert az('container', 'show', *scope, '-n', pool)['instanceView']['state'] == 'Stopped'
    run_probe(root, run, scope, args.layout)


def run_probe(root, run, scope, layout):
    groups = az('container', 'list', *scope)
    for group in groups:
        state = az('container', 'show', *scope, '-n', group['name'])['instanceView']['state']
        assert state in ('Stopped', 'Succeeded', 'Failed'), 'Another group is consuming the cloud pool'
    print('Closed state preserved; existing cloud groups stopped. Starting '+layout+' ingress probe.', flush=True)
    name = run['prefix'] + '-probe-' + datetime.now(timezone.utc).strftime('%H%M%S')
    identity = '/subscriptions/' + run['subscription'] + '/resourceGroups/DAWL/providers/Microsoft.ManagedIdentity/userAssignedIdentities/' + run['prefix'] + '-pull'
    image = json.loads((root / 'contribution-images.json').read_text())['gateway']
    payload = ('HYPERTRIX-INGRESS-' + name).encode()
    code = f'''from http.server import BaseHTTPRequestHandler,HTTPServer
import time
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  data={payload!r};self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
server=HTTPServer(('0.0.0.0',2222),Handler);server.timeout=1
end=time.monotonic()+600
while time.monotonic()<end:server.handle_request()
server.server_close()
'''
    body = dict(location='norwayeast', tags=dict(project='DAWL', pilot=run['prefix'], purpose='bounded-ingress-diagnostic'),
                identity=dict(type='UserAssigned', userAssignedIdentities={identity: {}}),
                properties=dict(osType='Linux', restartPolicy='Never',
                    imageRegistryCredentials=[dict(server=image.split('/')[0], identity=identity)],
                    ipAddress=dict(type='Public', dnsNameLabel=name, ports=[dict(port=2222, protocol='TCP')]),
                    containers=[dict(name='probe', properties=dict(image=image, command=['python','-u','-c',code],
                        ports=[dict(port=2222, protocol='TCP')], resources=dict(requests=dict(cpu=1,memoryInGB=1))))]))
    if layout in ('nine', 'nine-conservative', 'nine-gateway-last', 'eight-original', 'nine-omit-empty-ports', 'nine-no-limits', 'nine-aux-limits', 'nine-aux-public-ports'):
        specs = json.loads((root / 'restart-parameters.json').read_text())['parameters']['nodeSpecs']['value']
        specs += [dict(name='gateway',cpu=.3,memory_gb=.4),dict(name='console',cpu=.2,memory_gb=.5)]
        if layout == 'nine-conservative':
            for node in specs:
                if node['name']=='gateway':node.update(cpu=.5,memory_gb=.5)
                elif node['name']=='console':node.update(cpu=.25,memory_gb=.5)
                elif node['role']=='provider':node.update(cpu=.25,memory_gb=1.0)
        if layout == 'nine-gateway-last':
            specs.sort(key=lambda node:node['name']=='gateway')
        if layout == 'eight-original':
            specs = [dict(n) for n in json.loads((root / 'node-operator-config.json').read_text())['nodes'] if n['site']=='cloud']
            specs += [dict(name='gateway',cpu=.5,memory_gb=.5)]
        body['properties']['containers'] = [dict(name=n['name'], properties=dict(
            image=image, command=['python','-u','-c',code if n['name']=='gateway' else 'import time;time.sleep(600)'],
            ports=[dict(port=2222,protocol='TCP')] if n['name']=='gateway' else [],
            resources=dict(requests=dict(cpu=n['cpu'],memoryInGB=n['memory_gb']),limits=dict(cpu=3,memoryInGB=n['memory_gb'])))) for n in specs]
        assert sum(n['cpu'] for n in specs) <= 3.000001
        if layout in ('eight-original', 'nine-omit-empty-ports'):
            for container in body['properties']['containers']:
                if not container['properties']['ports']:
                    del container['properties']['ports']
        if layout == 'nine-no-limits':
            for container in body['properties']['containers']:
                del container['properties']['resources']['limits']
        if layout in ('nine-aux-limits', 'nine-aux-public-ports'):
            for container in body['properties']['containers']:
                if container['name'] in ('gateway','console'):
                    container['properties']['resources']['limits']['cpu'] = container['properties']['resources']['requests']['cpu']
        if layout == 'nine-aux-public-ports':
            extra = [dict(port=p,protocol='TCP') for p in (80,443)]
            body['properties']['ipAddress']['ports'] += extra
            next(c for c in body['properties']['containers'] if c['name']=='console')['properties']['ports'] = extra
    save(root / 'network-probe-request.json', body)
    save(root / ('network-probe-request-'+layout+'.json'), body)
    url = 'https://management.azure.com/subscriptions/' + run['subscription'] + '/resourceGroups/DAWL/providers/Microsoft.ContainerInstance/containerGroups/' + name + '?api-version=2023-05-01'
    save(root / 'network-probe-scope.json', dict(name=name, url=url))
    report = dict(name=name, layout=layout, at=datetime.now(timezone.utc).isoformat(), reachable=False)
    started = time.monotonic()
    try:
        az('rest', '--method', 'put', '--url', url, '--body', '@'+str(root / 'network-probe-request.json'))
        previous = None
        for _ in range(16):
            raw = az('rest', '--method', 'get', '--url', url)
            group = {**raw['properties'], 'name': raw['name']}
            group['containers'] = [{**c['properties'], 'name': c['name']} for c in raw['properties']['containers']]
            # This disposable group contains no secrets or node state. Retain
            # provisioning events, including errors that appear on timeout.
            save(root / ('network-probe-state-'+layout+'.json'), group)
            instance = group['containers'][0].get('instanceView') or {}
            state = (group['provisioningState'],(group.get('ipAddress') or {}).get('ip'),(instance.get('currentState') or {}).get('state'))
            if state != previous:
                print('Probe '+json.dumps(state), flush=True)
                previous = state
            report.update(provisioning=state[0], ip=state[1], container=state[2], elapsed_seconds=round(time.monotonic()-started,1),
                          states={c['name']: ((c.get('instanceView') or {}).get('currentState') or {}).get('state') for c in group['containers']})
            if state[1]:
                try:
                    http = build_opener(ProxyHandler({}))
                    with http.open('http://'+state[1]+':2222/',timeout=5) as response:
                        report['reachable'] = response.read(1000) == payload
                    if report['reachable']:break
                except Exception as error:
                    report['request_error'] = str(error)
            if state[0]=='Failed':break
            time.sleep(10)
        save(root / 'network-probe-result.json',report)
        save(root / ('network-probe-result-'+layout+'.json'),report)
        print(json.dumps(report),flush=True)
    finally:
        az('rest','--method','delete','--url',url)
        for _ in range(6):
            if not any(g['name']==name for g in az('container','list',*scope)):
                print('Temporary ingress probe removed; original cloud group remains safely stopped.',flush=True)
                break
            time.sleep(5)
        else:
            raise RuntimeError('Probe deletion is still pending; inspect it before starting another group')


if __name__=='__main__':
    main()
