"""Opt-in, immutable participation contracts and independent model branches.

Heterogeneous collaborations currently federate exact-replay execution cohorts.
They do not assert approximate equivalence between different environments.
This module handles metadata; signed environment declarations are not attestation
of hardware or proof that the signer executed the declared computation.
"""
from dawl_node.common import canonical_json, sha256
from data_ingestion.protocol import require_digest
from .contributions import descriptor

COMMANDS = {'activate_collaboration', 'register_collaboration',
            'accept_collaboration', 'register_model_branch'}
EXACT = 'full-replay-and-reduce/1'
LIBRARIES = {'torch', 'transformers', 'safetensors', 'tokenizers', 'numpy'}


def identity(body):
    return sha256(canonical_json(body))


def envelope(value):
    if set(value) != {'id', 'body'} or value['id'] != identity(value['body']):
        raise ValueError('content identity mismatch')
    return value['body']


def label(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 120 or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('invalid label')


def ordered(values, maximum):
    if not isinstance(values, list) or not 1 <= len(values) <= maximum or values != sorted(set(values)):
        raise ValueError('expected bounded, sorted, unique identities')
    for value in values: require_digest(value)


def validate_environment(value):
    b = envelope(value)
    if set(b) != {'schema', 'implementation_sha256', 'runtime', 'python', 'system', 'machine',
                  'torch_build_sha256', 'device', 'execution'} or b['schema'] != 'hypertrix.execution-environment/1':
        raise ValueError('invalid execution environment')
    for field in ('implementation_sha256', 'torch_build_sha256'): require_digest(b[field])
    if set(b['runtime']) != LIBRARIES: raise ValueError('incomplete runtime pins')
    for value in list(b['runtime'].values())+[b['python'], b['system'], b['machine']]: label(value)
    device = b['device']
    if set(device) != {'type', 'name', 'capability', 'driver'} or device['type'] not in ('cpu', 'cuda'):
        raise ValueError('unsupported device descriptor')
    for value in device.values(): label(value)
    if canonical_json(b['execution']) != canonical_json({'dtype': 'float32', 'attention': 'eager', 'compiler': 'eager/no-compile',
                          'tf32': False, 'deterministic': True, 'threads': 2,
                          'cublas_workspace_config': ':4096:8'}):
        raise ValueError('unsupported execution settings')
    return b


def validate_manifest(state, value):
    b = envelope(value)
    if set(b) != {'schema', 'name', 'mode', 'profiles', 'members', 'environments', 'verification', 'admission'}:
        raise ValueError('invalid collaboration fields')
    if (b['schema'] != 'hypertrix.collaboration-manifest/1' or b['mode'] not in ('homogeneous', 'heterogeneous')
            or b['verification'] != EXACT or b['admission'] != 'explicit-members/1'):
        raise ValueError('unsupported collaboration policy')
    label(b['name']); ordered(b['profiles'], 32); ordered(b['members'], 60)
    if not set(b['profiles']) <= state['profiles'].keys(): raise ValueError('unknown profiles')
    for public in b['members']:
        member = state['training_members'][public]
        if not set(b['profiles']) <= set(member['profiles']): raise ValueError('missing member profile capability')
    environments = b['environments']
    if not isinstance(environments, list) or not 1 <= len(environments) <= 8:
        raise ValueError('invalid environments')
    ordered([e['id'] for e in environments], 8)
    if b['mode'] == 'homogeneous' and len(environments) != 1:
        raise ValueError('homogeneous mode requires exactly one execution environment')
    for environment in environments:
        if validate_environment(environment)['implementation_sha256'] != state['collaboration_protocol']['implementation_sha256']:
            raise ValueError('environment implementation not activated')
    return b


def context(state, branch_id):
    branch = state['model_branches'][branch_id]
    manifest = state['collaborations'][branch['body']['collaboration']]
    return branch, manifest


def require_member(state, branch, public):
    b = branch['body']; manifest = state['collaborations'][b['collaboration']]
    acceptance = manifest['acceptances'].get(public)
    if not acceptance or acceptance['environment'] != b['environment']:
        raise ValueError('member has not accepted this manifest and execution cohort')


def validate_stage(state, body):
    branch, manifest = context(state, body['branch'])
    b = branch['body']
    if branch['active_stage'] is not None: raise ValueError('branch already has an active or disputed stage')
    if (body['parent_checkpoint'] != branch['head'] or body['initial_adapter'] != branch['adapter']
            or any(body[k] != b[k] for k in ('profile_id', 'token_blueprint', 'parent_model', 'model_contract'))):
        raise ValueError('stage must extend the exact branch head, model and Blueprint')
    for public in body['verifiers']: require_member(state, branch, public)
    trainers = [public for public, accepted in manifest['acceptances'].items()
                if accepted['environment'] == b['environment'] and state['training_members'][public]['role'] == 'TRAINER']
    if len(trainers) < body['shards']: raise ValueError('insufficient accepted trainers in this execution cohort')


def candidate_fields(state, stage):
    branch, _ = context(state, stage['body']['branch'])
    return {k: branch['body'][k] for k in ('collaboration', 'environment')} | {'branch': branch['id']}


def promoted(state, stage, record):
    branch, _ = context(state, stage['body']['branch'])
    if branch['active_stage'] != stage['id'] or branch['head'] != record['body']['parent']:
        raise ValueError('checkpoint would overwrite another branch head')
    branch['head'] = record['id']; branch['adapter'] = record['body']['adapter']
    branch['checkpoints'].append(record['id'])
    if stage['status'] == 'complete': branch['active_stage'] = None


def apply(state, public, hats, kind, payload, txid):
    publisher = 'PUBLISHER' in hats
    if kind == 'activate_collaboration':
        if (not publisher or not state.get('training_protocol') or state.get('collaboration_protocol')
                or any(s['status'] == 'active' for s in state['training_stages'].values())):
            raise ValueError('one-time publisher activation requires training protocol and drained active stages')
        if set(payload) != {'version', 'implementation_sha256'} or type(payload['version']) is not int or payload['version'] != 5:
            raise ValueError('invalid collaboration activation')
        require_digest(payload['implementation_sha256'])
        state.update(collaboration_protocol={**payload, 'height': state['height'], 'transaction': txid},
                     collaborations={}, model_branches={})
        return {'version': 5}
    if not state.get('collaboration_protocol'): raise ValueError('collaboration protocol is not active')
    if kind == 'register_collaboration':
        if not publisher or set(payload) != {'manifest'}: raise ValueError('publisher manifest registration required')
        value = payload['manifest']; validate_manifest(state, value)
        if value['id'] in state['collaborations'] or len(state['collaborations']) >= 16:
            raise ValueError('duplicate manifest or registry full')
        state['collaborations'][value['id']] = {**value, 'owner': public, 'acceptances': {}, 'transaction': txid}
        return {'collaboration': value['id']}
    if kind == 'accept_collaboration':
        if set(payload) != {'collaboration', 'environment'}: raise ValueError('invalid participation acceptance')
        manifest = state['collaborations'][payload['collaboration']]
        if (public not in manifest['body']['members'] or public in manifest['acceptances']
                or payload['environment'] not in [e['id'] for e in manifest['body']['environments']]):
            raise ValueError('uninvited member, repeated acceptance or unapproved environment')
        manifest['acceptances'][public] = {**payload, 'transaction': txid, 'height': state['height']}
        return {'accepted': True, **payload}
    if kind == 'register_model_branch':
        if set(payload) != {'branch'}: raise ValueError('invalid branch registration')
        value = payload['branch']; b = envelope(value)
        if (set(b) != {'schema', 'name', 'collaboration', 'environment', 'profile_id', 'token_blueprint',
                      'parent', 'parent_model', 'model_contract', 'initial_adapter'} or b['schema'] != 'hypertrix.model-branch/1'):
            raise ValueError('invalid model branch fields')
        label(b['name'])
        for field in ('collaboration', 'environment', 'profile_id', 'token_blueprint', 'parent_model', 'model_contract'): require_digest(b[field])
        descriptor(b['initial_adapter'], 16*1024*1024)
        manifest = state['collaborations'][b['collaboration']]
        if public != manifest['owner']:
            if manifest['body']['mode'] != 'heterogeneous' or state['training_members'][public]['role'] != 'TRAINER':
                raise ValueError('manifest owner or accepted heterogeneous trainer required')
            require_member(state, {'body': b}, public)
        if b['profile_id'] not in manifest['body']['profiles'] or b['environment'] not in [e['id'] for e in manifest['body']['environments']]:
            raise ValueError('branch outside collaboration policy')
        if not any(h['id'] == b['token_blueprint'] for h in state['profile_releases'].get(b['profile_id'], [])):
            raise ValueError('branch needs a certified Blueprint')
        if b['parent'] is not None:
            require_digest(b['parent'])
            parent = state['training_checkpoints'][b['parent']]
            previous = state['training_stages'][parent['body']['stage']]['body']
            if (b['initial_adapter'] != parent['body']['adapter']
                    or any(b[k] != previous.get(k) for k in ('parent_model', 'profile_id', 'model_contract'))):
                raise ValueError('fork must preserve parent model, profile and adapter')
        if value['id'] in state['model_branches'] or len(state['model_branches']) >= 64:
            raise ValueError('duplicate branch or registry full')
        state['model_branches'][value['id']] = {**value, 'head': b['parent'] or value['id'],
            'adapter': b['initial_adapter'], 'active_stage': None, 'checkpoints': [], 'transaction': txid, 'creator': public}
        return {'branch': value['id']}
    raise ValueError('unsupported collaboration command')


def select_checkpoint(state, selection):
    """Resolve explicit pins from already certificate-verified state, never 'best'."""
    if set(selection) != {'branch', 'checkpoint', 'collaboration', 'environment', 'profile_id', 'verification'}:
        raise ValueError('consumer selection must pin branch, checkpoint and policy')
    branch, manifest = context(state, selection['branch'])
    if any(selection[k] != branch['body'][k] for k in ('collaboration', 'environment', 'profile_id')):
        raise ValueError('consumer policy pins differ from branch')
    if selection['verification'] != EXACT or selection['checkpoint'] not in branch['checkpoints']:
        raise ValueError('checkpoint was not replay-attested on this branch')
    record = state['training_checkpoints'][selection['checkpoint']]
    stage = state['training_stages'][record['body']['stage']]
    if (record['body']['verification'] != EXACT or record['body']['branch'] != branch['id']
            or set(record['receipts']) != set(stage['body']['verifiers'])):
        raise ValueError('incomplete checkpoint verification')
    return {**selection, 'mode': manifest['body']['mode'], 'adapter': record['body']['adapter'],
            'parent_model': stage['body']['parent_model'], 'model_contract': stage['body']['model_contract'],
            'token_blueprint': stage['body']['token_blueprint'],
            'execution_manifest': stage['body']['manifest'], 'parent': record['body']['parent'],
            'chain_id': state['chain_id'], 'plan_sha256': state['plan_sha256'],
            'scope': 'Exact replay attested by named admitted verifiers; not a proof of training or quality score.'}


def summary(state):
    return {'protocol': state.get('collaboration_protocol'),
            'manifests': {i: {'name': m['body']['name'], 'mode': m['body']['mode'],
                              'members': len(m['body']['members']), 'accepted': len(m['acceptances']),
                              'environments': len(m['body']['environments'])}
                          for i, m in state.get('collaborations', {}).items()},
            'branches': {i: {'name': b['body']['name'], 'collaboration': b['body']['collaboration'],
                             'environment': b['body']['environment'], 'parent': b['body']['parent'],
                             'head': b['head'], 'checkpoints': len(b['checkpoints']), 'active_stage': b['active_stage']}
                         for i, b in state.get('model_branches', {}).items()}}
