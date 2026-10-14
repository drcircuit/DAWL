"""Fixed pilot identities: one 3-vCPU Azure pool and a separate local pool."""


def members():
    nodes = [dict(name=f'validator-{i}', role='validator', host=f'127.70.0.{10+i}',
                  site='cloud' if i < 2 else 'local') for i in range(4)]
    nodes += [dict(name=f'provider-{i}', role='provider', host=f'127.70.0.{20+i}',
                   site='cloud' if i == 0 else 'local') for i in range(2)]
    nodes += [dict(name='publisher', role='publisher', host='127.70.0.30', site='local')]
    nodes += [dict(name=f'worker-{i}', role='worker', host=f'127.70.0.{40+i}',
                   site='cloud' if i < 4 else 'local') for i in range(20)]
    return nodes


def endpoints(site, nodes=None, discovery=False):
    result = [(m['host'], p) for m in (members() if nodes is None else nodes) if m['site'] == site
            for p in ([26656, 26657] if m['role'] == 'validator' else [8900] if m['role'] == 'provider' else [])]
    if nodes is not None and site=='cloud': result.append(('127.70.0.31',8901))
    if discovery:
        result += [(m['host'],8903) for m in nodes if m['site']==site]
        if site=='local':result += [('127.70.0.22',8900),('127.70.0.22',8903),('127.70.0.23',8903),('127.70.0.32',8902)]
    return result
