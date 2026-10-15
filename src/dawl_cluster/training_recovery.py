"""Opt-in recovery for governed training stages; never relaxes replay quorum.

Failure reports are signed observations, not proof of misconduct. The existing
collaboration owner can discard work or cancel a stalled stage, but cannot use
these commands to promote a checkpoint or edit an immutable verifier roster.
"""
from data_ingestion.protocol import require_digest
from .profile_protocol import integer
from . import collaboration

SCHEMA = 'hypertrix.oracle-training-stage/3'
COMMANDS = {'activate_training_recovery', 'report_training_failure',
            'reject_training_update', 'cancel_training_stage'}
REASONS = {'artifact_unavailable', 'replay_mismatch'}


def enabled(stage):
    return stage['body']['schema'] == SCHEMA


def validate(state, body):
    if not state.get('training_recovery_protocol'):
        raise ValueError('recovery activation required')
    policy = body['recovery']
    if set(policy) != {'round_timeout_blocks', 'max_rejections', 'supersedes'}:
        raise ValueError('invalid recovery policy')
    integer(policy['round_timeout_blocks'], 10, 6000)
    integer(policy['max_rejections'], 1, 32)
    if policy['supersedes'] is not None:
        previous = state['training_stages'][policy['supersedes']]
        if (previous['status'] != 'cancelled' or not enabled(previous)
                or previous['body']['branch'] != body['branch']
                or previous['head'] != body['parent_checkpoint']):
            raise ValueError('replacement must extend a cancelled stage on this branch')


def initialize(state, stage):
    stage.update(recovery_revision=0, recovery_history=[], failure_reports={},
                 round_started_height=state['height'])


def pending_failures(state, stage):
    current = {j['contribution']['transaction'] for j in state['training_jobs'].values()
               if j['spec']['stage'] == stage['id'] and j['spec']['round'] == stage['round'] and j['contribution']}
    return [r for r in stage.get('failure_reports', {}).values() if r['contribution'] in current]


def report_payload(stage, job_id, job, reason, evidence):
    return {'stage': stage['id'], 'round': stage['round'], 'revision': stage['recovery_revision'],
            'job': job_id, 'contribution': job['contribution']['transaction'],
            'reason': reason, 'evidence': evidence}


def apply(state, public, hats, kind, payload, txid):
    if kind == 'activate_training_recovery':
        if ('PUBLISHER' not in hats or not state.get('collaboration_protocol')
                or state.get('training_recovery_protocol')
                or any(s['status'] in ('active', 'disputed') for s in state['training_stages'].values())):
            raise ValueError('one-time publisher activation requires drained governed training')
        if (set(payload) != {'version', 'implementation_sha256'} or type(payload['version']) is not int
                or payload['version'] != 6
                or payload['implementation_sha256'] != state['collaboration_protocol']['implementation_sha256']):
            raise ValueError('recovery must use the pinned collaboration implementation')
        state['training_recovery_protocol'] = {**payload, 'height': state['height'], 'transaction': txid}
        return {'version': 6}
    stage = state['training_stages'][payload['stage']]
    if not state.get('training_recovery_protocol') or not enabled(stage) or stage['status'] not in ('active', 'disputed'):
        raise ValueError('recoverable active or disputed stage required')
    if (type(payload['round']) is not int or payload['round'] != stage['round']
            or type(payload['revision']) is not int or payload['revision'] != stage['recovery_revision']):
        raise ValueError('stale recovery round or revision')
    branch, manifest = collaboration.context(state, stage['body']['branch'])
    if kind == 'report_training_failure':
        if (set(payload) != {'stage', 'round', 'revision', 'job', 'contribution', 'reason', 'evidence'}
                or public not in stage['body']['verifiers'] or payload['reason'] not in REASONS):
            raise ValueError('named verifier and bounded failure reason required')
        collaboration.require_member(state, branch, public)
        require_digest(payload['evidence'])
        job = state['training_jobs'][payload['job']]
        if (job['spec']['stage'] != stage['id'] or job['spec']['round'] != stage['round']
                or not job['contribution'] or job['contribution']['transaction'] != payload['contribution']):
            raise ValueError('failure must identify a current submitted contribution')
        if (len(stage['failure_reports']) >= 2048 or any(r['signer'] == public and r['contribution'] == payload['contribution']
                for r in stage['failure_reports'].values())):
            raise ValueError('duplicate report or report registry full')
        stage['failure_reports'][txid] = {**payload, 'signer': public, 'height': state['height'], 'transaction': txid}
        return {'report': txid, 'checkpoint_promoted': False}
    if public != manifest['owner'] or 'PUBLISHER' not in hats:
        raise ValueError('collaboration owner recovery decision required')
    event = {**payload, 'signer': public, 'transaction': txid, 'height': state['height'],
             'kind': kind, 'votes': stage['votes']}
    if kind == 'reject_training_update':
        if set(payload) != {'stage', 'round', 'revision', 'report'} or stage['status'] != 'active':
            raise ValueError('active stage and signed failure reference required')
        if stage['recovery_revision'] >= stage['body']['recovery']['max_rejections']:
            raise ValueError('rejection budget exhausted; cancel and register a replacement stage')
        report = stage['failure_reports'][payload['report']]
        job = state['training_jobs'][report['job']]
        contribution = job['contribution']
        if (report['round'] != stage['round'] or not contribution
                or contribution['transaction'] != report['contribution']):
            raise ValueError('report no longer identifies current work')
        event['contribution'] = contribution
        job.setdefault('rejected_trainers', []).append(contribution['signer'])
        job['contribution'] = None
        stage['votes'] = {}
        stage['recovery_revision'] += 1
        stage['recovery_history'].append(event)
        return {'job': report['job'], 'revision': stage['recovery_revision'], 'checkpoint_promoted': False}
    if kind == 'cancel_training_stage':
        if set(payload) != {'stage', 'round', 'revision', 'reason'} or payload['reason'] not in ('round_timeout', 'disputed'):
            raise ValueError('bounded cancellation reason required')
        if payload['reason'] == 'disputed':
            if stage['status'] != 'disputed': raise ValueError('stage is not disputed')
        elif state['height'] < stage['round_started_height'] + stage['body']['recovery']['round_timeout_blocks']:
            raise ValueError('round deadline has not elapsed')
        if branch['active_stage'] != stage['id'] or branch['head'] != stage['head']:
            raise ValueError('branch head changed')
        event['unfinished_jobs'] = sorted(i for i, j in state['training_jobs'].items()
                                          if j['spec']['stage'] == stage['id'] and j['spec']['round'] == stage['round'])
        for job in state['training_jobs'].values():
            if job['spec']['stage'] == stage['id']: job['leases'].clear()
        stage['recovery_history'].append(event)
        stage['status'] = 'cancelled'
        branch['active_stage'] = None
        return {'stage': stage['id'], 'status': 'cancelled', 'head': stage['head'], 'checkpoint_promoted': False}
    raise ValueError('unknown recovery command')
