"""Protocol 3: independent token-corpus namespaces on the shared Oracle chain.

Pure validation only: no tokenizer execution, artifact I/O or local runtime checks.
Worker receipts are attestations by admitted keys, never proofs of computation.
"""
from dawl_node.common import canonical_json, sha256
from data_ingestion.protocol import require_digest
from dawl_profiles.profiles import validate_profile, descriptor, content_id, validate_weights

COMMANDS = {'activate_profiles', 'register_profile', 'contribute_profile', 'claim_profile',
            'renew_profile', 'result_profile', 'publish_profile', 'register_profile_weights'}


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high: raise ValueError('integer outside protocol bound')


def done(state, job):
    return len(job['attestations']) >= state['plan']['replicas'] and not any(f['outcome'] == 'invalid' for f in job['failures'])


def active_jobs(state):
    return state.get('profile_jobs', {})


def validate_catalog(value, profile_id):
    if (set(value) != {'schema', 'profile_id', 'blueprint', 'checkpoint', 'books'}
            or value['schema'] != 'hypertrix.profile-catalog/1' or value['profile_id'] != profile_id):
        raise ValueError('wrong profile catalog')
    require_digest(value['blueprint'])
    cp = value['checkpoint']
    if set(cp) != {'policy', 'entries', 'set_sha256'}: raise ValueError('invalid checkpoint')
    require_digest(cp['policy']); require_digest(cp['set_sha256']); integer(cp['entries'], 3, 256)
    books = value['books']
    if not isinstance(books, list) or not 1 <= len(books) <= 32: raise ValueError('bounded contribution needs 1..32 books')
    ids = []
    for book in books:
        if set(book) != {'book_id', 'source_sha256', 'tokens_entry', 'frags', 'tokens'}:
            raise ValueError('invalid book catalog')
        integer(book['book_id'], 1, 1_000_000); integer(book['frags'], 1, 2050); integer(book['tokens'], 0, 8*1024*1024)
        require_digest(book['source_sha256']); require_digest(book['tokens_entry']); ids.append(book['book_id'])
    if ids != sorted(set(ids)): raise ValueError('books must be unique and ordered')


def job_spec(profile_id, contribution, book):
    return {'kind': 'profile-token-replay/v1', 'profile_id': profile_id,
            'contribution': contribution, **book}


def apply(state, public, hats, kind, payload, txid):
    publisher, auditor = 'PUBLISHER' in hats, 'AUDIT_CPU' in hats
    if kind == 'activate_profiles':
        if (not publisher or state.get('profile_protocol') or not state.get('protocol_upgrade')
                or not state['dataset_release'] or not all(done(state, j) and not j['leases'] for j in state['jobs'].values())):
            raise ValueError('v3 activation needs publisher, v2 release and drained completed jobs')
        if set(payload) != {'version', 'implementation_sha256', 'runtime_manifest'} or type(payload['version']) is not int or payload['version'] != 3:
            raise ValueError('unsupported activation')
        require_digest(payload['implementation_sha256']); require_digest(payload['runtime_manifest'])
        state.update(profile_protocol={**payload, 'height': state['height'], 'transaction': txid,
                                      'legacy_release': state['dataset_release']['id']},
                     profiles={}, profile_contributions={}, profile_jobs={}, profile_heads={},
                     profile_releases={}, profile_weights={})
        return {'version': 3}
    if not state.get('profile_protocol'): raise ValueError('profile protocol is not active')
    if kind == 'register_profile':
        if not publisher or set(payload) != {'profile'}: raise ValueError('publisher profile registration required')
        profile = payload['profile']; validate_profile(profile)
        if profile['id'] in state['profiles'] or len(state['profiles']) >= 32: raise ValueError('duplicate or full profile registry')
        state['profiles'][profile['id']] = profile
        state['profile_heads'][profile['id']] = None
        state['profile_releases'][profile['id']] = []
        return {'profile_id': profile['id']}
    if kind == 'claim_profile':
        if not auditor or set(payload) != {'profile_ids'}: raise ValueError('auditor capability required')
        supported = payload['profile_ids']
        if not isinstance(supported, list) or not 1 <= len(supported) <= 32 or supported != sorted(set(supported)):
            raise ValueError('invalid supported profile list')
        if not set(supported) <= state['profiles'].keys(): raise ValueError('unknown profile capability')
        for identity, job in sorted(state['profile_jobs'].items()):
            if public in job['leases']:
                if job['spec']['profile_id'] not in supported: raise ValueError('finish or expire the previous profile lease first')
                return {'job': identity, 'profile_id': job['spec']['profile_id'], **job['leases'][public]}
        candidates = [(len(j['attestations'])+len(j['leases']), identity, j)
            for identity, j in state['profile_jobs'].items() if j['spec']['profile_id'] in supported
            and not done(state, j) and public not in j['attestations']
            and len(j['attestations'])+len(j['leases']) < state['plan']['replicas']
            and not any(f['outcome'] == 'invalid' for f in j['failures'])]
        if not candidates: return {'job': None}
        _, identity, job = min(candidates, key=lambda row: (row[0], row[1]))
        lease = {'generation': txid, 'expires': state['height']+state['plan']['lease_blocks']}
        job['leases'][public] = lease; job['attempts'] += 1
        return {'job': identity, 'profile_id': job['spec']['profile_id'], **lease}
    profile_id = payload['profile_id']; require_digest(profile_id)
    profile = state['profiles'][profile_id]
    if kind == 'contribute_profile':
        if public not in state['providers'] or set(payload) != {'profile_id', 'bundle', 'catalog'}:
            raise ValueError('registered Cortex provider required')
        descriptor(payload['bundle']); integer(payload['bundle']['bytes'], 1, 4*1024*1024)
        validate_catalog(payload['catalog'], profile_id)
        batches = state['profile_contributions']
        if len(batches) >= 256 or len(state['profile_jobs'])+len(payload['catalog']['books']) > 10000:
            raise ValueError('profile queue is full')
        previous = {b['book_id'] for c in batches.values() if c['profile_id'] == profile_id for b in c['catalog']['books']}
        if previous.intersection(b['book_id'] for b in payload['catalog']['books']):
            raise ValueError('book already contributed to this profile')
        identity = content_id({'provider': public, **payload})
        batches[identity] = {'provider': public, **payload, 'transaction': txid, 'height': state['height']}
        for book in payload['catalog']['books']:
            spec = job_spec(profile_id, identity, book)
            state['profile_jobs'][content_id(spec)] = {'spec': spec, 'leases': {}, 'attestations': {}, 'failures': [], 'attempts': 0}
        return {'contribution': identity, 'profile_id': profile_id}
    if kind in ('renew_profile', 'result_profile'):
        if not auditor: raise ValueError('auditor capability required')
        fields = {'profile_id', 'job', 'generation'} | ({'report'} if kind == 'result_profile' else set())
        if set(payload) != fields: raise ValueError('invalid profile receipt/renewal')
        job = state['profile_jobs'][payload['job']]; spec = job['spec']; lease = job['leases'].get(public)
        if (spec['profile_id'] != profile_id or not lease or lease['generation'] != payload['generation']
                or lease['expires'] < state['height']): raise ValueError('wrong profile, owner or stale lease')
        if kind == 'renew_profile':
            lease['expires'] = state['height']+state['plan']['lease_blocks']
            return {'job': payload['job'], 'profile_id': profile_id, **lease}
        report = payload['report']
        if set(report) != {'outcome', 'checked_chunks', 'tokens_checked', 'implementation_sha256', 'execution_ms', 'error'}:
            raise ValueError('invalid token-audit report')
        if report['implementation_sha256'] != state['profile_protocol']['implementation_sha256']:
            raise ValueError('wrong worker implementation')
        if report['outcome'] not in ('pass', 'invalid', 'error', 'unavailable') or not isinstance(report['error'], str) or len(report['error']) > 512:
            raise ValueError('invalid audit outcome')
        for field in ('checked_chunks', 'tokens_checked', 'execution_ms'): integer(report[field], 0, 2**50)
        if report['outcome'] == 'pass':
            if report['checked_chunks'] != spec['frags'] or report['tokens_checked'] != spec['tokens'] or report['error']:
                raise ValueError('receipt does not match profile coverage')
            job['attestations'][public] = {'transaction': txid, 'height': state['height'], 'report': report}
        else:
            job['failures'] = (job['failures']+[dict(report, signer=public, transaction=txid, height=state['height'])])[-64:]
        del job['leases'][public]
        return {'job': payload['job'], 'profile_id': profile_id, 'complete': done(state, job)}
    if kind == 'publish_profile':
        if not publisher or set(payload) != {'profile_id'}: raise ValueError('publisher required')
        batches = {i: c for i, c in state['profile_contributions'].items() if c['profile_id'] == profile_id}
        jobs = [j for j in state['profile_jobs'].values() if j['spec']['profile_id'] == profile_id]
        if not jobs or not all(done(state, j) for j in jobs): raise ValueError('profile needs complete independent audit coverage')
        previous = state['profile_heads'][profile_id]
        if previous and previous['body']['contributions'] == sorted(batches): return {'profile_id': profile_id, 'blueprint': previous['id']}
        body = {'schema': 'hypertrix.certified-token-blueprint/1', 'profile_id': profile_id,
                'plan_sha256': state['plan_sha256'], 'parent_blueprint': previous['id'] if previous else None,
                'contributions': sorted(batches), 'local_blueprints': sorted(c['catalog']['blueprint'] for c in batches.values()),
                'books': sorted((b for c in batches.values() for b in c['catalog']['books']), key=lambda b: b['book_id']),
                'replicas': state['plan']['replicas'],
                'receipts': sorted(a['transaction'] for j in jobs for a in j['attestations'].values()),
                'scope': 'Token provenance and replay attested by distinct admitted keys; no vector audit, training or proof of computation.'}
        record = {'id': content_id(body), 'body': body, 'height': state['height']}
        state['profile_heads'][profile_id] = record; state['profile_releases'][profile_id].append(record)
        return {'profile_id': profile_id, 'blueprint': record['id']}
    if kind == 'register_profile_weights':
        if not publisher or set(payload) != {'profile_id', 'manifest'}: raise ValueError('publisher weight registration required')
        identity = validate_weights(payload['manifest'], profile)
        if identity in state['profile_weights'] or len(state['profile_weights']) >= 128: raise ValueError('duplicate or full weights registry')
        state['profile_weights'][identity] = {'profile_id': profile_id, 'manifest': payload['manifest'],
            'height': state['height'], 'transaction': txid, 'artifact_bytes_verified': False, 'training_history_verified': False}
        return {'profile_id': profile_id, 'weight_manifest': identity}
    raise ValueError('unsupported profile command')


def summary(state):
    return {pid: {'label': profile['body']['label'], 'blueprint': state['profile_heads'][pid],
                  'jobs': sum(j['spec']['profile_id'] == pid for j in state['profile_jobs'].values()),
                  'complete': sum(j['spec']['profile_id'] == pid and done(state, j) for j in state['profile_jobs'].values()),
                  'leases': sum(len(j['leases']) for j in state['profile_jobs'].values() if j['spec']['profile_id'] == pid)}
            for pid, profile in state.get('profiles', {}).items()}
