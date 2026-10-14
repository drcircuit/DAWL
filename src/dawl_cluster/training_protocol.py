"""Oracle training queues. Deterministic metadata only, never tensor execution.

The publisher admits identities and stages. Work ownership is temporary. Every
round requires distinct trainers and all named independent verifier signatures.
Those signatures attest to replay and replication; consensus is not a proof of
training or long-term availability. A conflicting verification freezes a stage.
"""
from dawl_node.common import canonical_json, sha256
from data_ingestion.protocol import require_digest
from .contributions import descriptor
from .profile_protocol import integer
from . import collaboration

COMMANDS = {'activate_training', 'admit_training_worker', 'register_training_stage',
            'claim_training', 'renew_training', 'result_training', 'verify_training_round'}


def identity(body):
    return sha256(canonical_json(body))


def active_jobs(state):
    return state.get('training_jobs', {})


def round_jobs(state, stage):
    return sorted(((i, j) for i, j in state['training_jobs'].items()
                   if j['spec']['stage'] == stage['id'] and j['spec']['round'] == stage['round']),
                  key=lambda pair: pair[1]['spec']['shard'])


def enqueue(state, stage):
    b = stage['body']; r = stage['round']
    for item in b['work'][r*b['shards']:(r+1)*b['shards']]:
        spec = {'stage': stage['id'], 'parent': stage['head'], **item}
        state['training_jobs'][identity(spec)] = {'spec': spec, 'leases': {}, 'contribution': None, 'attempts': 0}


def validate_stage(state, stage):
    if set(stage) != {'id', 'body'} or stage['id'] != identity(stage['body']):
        raise ValueError('stage identity mismatch')
    b = stage['body']
    governed = b['schema'] == 'hypertrix.oracle-training-stage/2'
    fields = {'schema', 'profile_id', 'token_blueprint', 'manifest', 'initial_adapter',
              'parent_model', 'rounds', 'shards', 'local_steps', 'work', 'verifiers', 'lease_blocks'}
    if set(b) != fields | ({'branch', 'parent_checkpoint', 'model_contract'} if governed else set()):
        raise ValueError('invalid training stage fields')
    if governed:
        if not state.get('collaboration_protocol'): raise ValueError('collaboration activation required')
    elif b['schema'] != 'hypertrix.oracle-training-stage/1' or state.get('collaboration_protocol'):
        raise ValueError('unsupported or ungoverned stage')
    for field in ('profile_id', 'token_blueprint', 'parent_model'): require_digest(b[field])
    if not any(h['id'] == b['token_blueprint'] for h in state['profile_releases'].get(b['profile_id'], [])):
        raise ValueError('stage needs a certified token Blueprint in this profile')
    descriptor(b['manifest'], 4*1024*1024); descriptor(b['initial_adapter'], 16*1024*1024)
    integer(b['rounds'], 1, 16); integer(b['shards'], 2, 8); integer(b['local_steps'], 1, 8)
    integer(b['lease_blocks'], 10, 600)
    verifiers = b['verifiers']
    if not isinstance(verifiers, list) or not 2 <= len(verifiers) <= 4 or verifiers != sorted(set(verifiers)):
        raise ValueError('stage needs 2..4 distinct ordered verifiers')
    for public in verifiers:
        member = state['training_members'][public]
        if member['role'] != 'VERIFIER' or b['profile_id'] not in member['profiles']:
            raise ValueError('verifier lacks admitted profile capability')
    if not isinstance(b['work'], list) or len(b['work']) != b['rounds']*b['shards']:
        raise ValueError('incomplete work catalog')
    width = 2*b['local_steps']
    for index, work in enumerate(b['work']):
        if (set(work) != {'round', 'shard', 'start', 'sequences', 'target_tokens', 'data'}
                or any(type(work[k]) is not int for k in ('round', 'shard', 'start', 'sequences'))
                or (work['round'], work['shard'], work['start'], work['sequences']) !=
                   (index//b['shards'], index % b['shards'], index*width, width)):
            raise ValueError('work catalog must be contiguous, disjoint and canonical')
        require_digest(work['data']); integer(work['target_tokens'], width, width*128)
    if len({w['data'] for w in b['work']}) != len(b['work']): raise ValueError('duplicate data shard')
    if governed: collaboration.validate_stage(state, b)
    return b


def candidate(state, stage, adapter):
    jobs = round_jobs(state, stage)
    if len(jobs) != stage['body']['shards'] or any(not j['contribution'] for _, j in jobs):
        raise ValueError('round has unfinished shards')
    if len({j['contribution']['signer'] for _, j in jobs}) != len(jobs):
        raise ValueError('round requires distinct trainers')
    descriptor(adapter, 16*1024*1024)
    if adapter['bytes'] != stage['body']['initial_adapter']['bytes']: raise ValueError('wrong adapter size')
    value = {'schema': 'hypertrix.oracle-weight-round/1', 'stage': stage['id'],
            'round': stage['round'], 'parent': stage['head'], 'adapter': adapter,
            'updates': [j['contribution']['transaction'] for _, j in jobs],
            'target_tokens': sum(j['spec']['target_tokens'] for _, j in jobs),
            'verification': 'full-replay-and-reduce/1'}
    if 'branch' in stage['body']:
        value.update(schema='hypertrix.oracle-weight-round/2', **collaboration.candidate_fields(state, stage))
    return value


def apply(state, public, hats, kind, payload, txid):
    publisher = 'PUBLISHER' in hats
    if kind == 'activate_training':
        if not publisher or not state.get('profile_protocol') or state.get('training_protocol'):
            raise ValueError('one-time publisher activation after protocol 3 required')
        if set(payload) != {'version', 'implementation_sha256', 'runtime_manifest'} or type(payload['version']) is not int or payload['version'] != 4:
            raise ValueError('invalid training activation')
        require_digest(payload['implementation_sha256']); require_digest(payload['runtime_manifest'])
        state.update(training_protocol={**payload, 'height': state['height'], 'transaction': txid},
                     training_members={}, training_stages={}, training_jobs={}, training_checkpoints={})
        return {'version': 4}
    if not state.get('training_protocol'): raise ValueError('training protocol is not active')
    if kind == 'admit_training_worker':
        if not publisher or set(payload) != {'public', 'role', 'profiles'}:
            raise ValueError('publisher admission required')
        member = payload['public']; require_digest(member)
        profiles = payload['profiles']
        if (member in state['actors'] or len(state['actors']) >= 64 or payload['role'] not in ('TRAINER', 'VERIFIER')
                or not isinstance(profiles, list) or not 1 <= len(profiles) <= 32 or profiles != sorted(set(profiles))
                or not set(profiles) <= state['profiles'].keys()):
            raise ValueError('invalid member, role or profile capability')
        state['training_members'][member] = {**payload, 'height': state['height'], 'transaction': txid}
        state['actors'][member] = {'sequence': 0, 'last_tx': None, 'last_result': None}
        return {'member': member}
    if kind == 'register_training_stage':
        if not publisher or set(payload) != {'stage'}: raise ValueError('publisher stage registration required')
        value = payload['stage']; b = validate_stage(state, value)
        if 'branch' in b and collaboration.context(state, b['branch'])[1]['owner'] != public:
            raise ValueError('collaboration owner must register its stage')
        if value['id'] in state['training_stages'] or len(state['training_stages']) >= 8:
            raise ValueError('duplicate stage or bounded stage registry full')
        stage = {**value, 'round': 0, 'head': b.get('parent_checkpoint', value['id']), 'adapter': b['initial_adapter'],
                 'status': 'active', 'votes': {}, 'checkpoints': [], 'transaction': txid, 'height': state['height']}
        if 'branch' in b: state['model_branches'][b['branch']]['active_stage'] = value['id']
        state['training_stages'][value['id']] = stage; enqueue(state, stage)
        return {'stage': value['id']}
    stage = state['training_stages'][payload['stage']]
    if stage['status'] != 'active': raise ValueError('training stage is not active')
    member = state['training_members'][public]
    if stage['body']['profile_id'] not in member['profiles']: raise ValueError('wrong profile capability')
    if 'branch' in stage['body']:
        collaboration.require_member(state, state['model_branches'][stage['body']['branch']], public)
    jobs = round_jobs(state, stage)
    if kind == 'claim_training':
        if member['role'] != 'TRAINER' or set(payload) != {'stage'}: raise ValueError('trainer required')
        # At most one outstanding lease across stages; one contribution per round.
        for i, job in sorted(state['training_jobs'].items()):
            if public in job['leases']:
                if job['spec']['stage'] != stage['id']: raise ValueError('finish previous stage lease first')
                return {'stage': stage['id'], 'job': i, **job['leases'][public]}
        if any(j['contribution'] and j['contribution']['signer'] == public for _, j in jobs): return {'job': None}
        for i, job in jobs:
            if job['contribution'] is None and not job['leases']:
                lease = {'generation': txid, 'expires': state['height']+stage['body']['lease_blocks']}
                job['leases'][public] = lease; job['attempts'] += 1
                return {'stage': stage['id'], 'job': i, **lease}
        return {'job': None}
    if kind in ('renew_training', 'result_training'):
        fields = {'stage', 'job', 'generation'} | ({'parent', 'adapter'} if kind == 'result_training' else set())
        if member['role'] != 'TRAINER' or set(payload) != fields: raise ValueError('invalid trainer receipt')
        job = state['training_jobs'][payload['job']]; lease = job['leases'].get(public)
        if (job['spec']['stage'] != stage['id'] or job['spec']['round'] != stage['round'] or job['contribution']
                or not lease or lease['generation'] != payload['generation'] or lease['expires'] < state['height']):
            raise ValueError('wrong owner, stage, round or stale lease generation')
        if kind == 'renew_training':
            lease['expires'] = state['height']+stage['body']['lease_blocks']
            return {'stage': stage['id'], 'job': payload['job'], **lease}
        descriptor(payload['adapter'], 16*1024*1024)
        if payload['parent'] != stage['head'] or payload['adapter']['bytes'] != stage['body']['initial_adapter']['bytes']:
            raise ValueError('wrong parent checkpoint or adapter size')
        job['contribution'] = {**payload, 'signer': public, 'transaction': txid, 'height': state['height']}
        job['leases'].clear()
        return {'job': payload['job'], 'accepted': True, 'checkpoint_promoted': False}
    if kind == 'verify_training_round':
        if member['role'] != 'VERIFIER' or public not in stage['body']['verifiers'] or set(payload) != {'stage', 'candidate'}:
            raise ValueError('named independent verifier required')
        body = payload['candidate']; expected = candidate(state, stage, body['adapter'])
        if body != expected or public in stage['votes']: raise ValueError('wrong round, parent, roster or repeated verification')
        # Distinct verifier role cannot also hold a training identity under this key.
        stage['votes'][public] = {'candidate': body, 'transaction': txid, 'height': state['height']}
        if len({identity(v['candidate']) for v in stage['votes'].values()}) > 1:
            stage['status'] = 'disputed'
            return {'stage': stage['id'], 'status': 'disputed'}
        if set(stage['votes']) == set(stage['body']['verifiers']):
            value = {'body': body, 'receipts': {p: stage['votes'][p] for p in sorted(stage['votes'])}}
            record = {'id': identity(value), **value}
            state['training_checkpoints'][record['id']] = record
            stage['head'] = record['id']; stage['adapter'] = body['adapter']
            stage['checkpoints'].append(record['id']); stage['round'] += 1; stage['votes'] = {}
            stage['status'] = 'complete' if stage['round'] == stage['body']['rounds'] else 'active'
            if 'branch' in stage['body']: collaboration.promoted(state, stage, record)
            if stage['status'] == 'active': enqueue(state, stage)
        return {'stage': stage['id'], 'status': stage['status'], 'head': stage['head']}
    raise ValueError('unknown training command')


def summary(state):
    return {i: {'status': s['status'], 'round': s['round'], 'rounds': s['body']['rounds'],
                'head': s['head'], 'shards': s['body']['shards'], 'profile_id': s['body']['profile_id'],
                'accepted_updates': sum(j['contribution'] is not None for j in state['training_jobs'].values() if j['spec']['stage'] == i),
                'active_leases': sum(len(j['leases']) for j in state['training_jobs'].values() if j['spec']['stage'] == i)}
            for i, s in state.get('training_stages', {}).items()}
