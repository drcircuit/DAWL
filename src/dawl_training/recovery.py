"""Worker-side failure evidence; usable without a GPU or a model import."""
from dawl_node.common import canonical_json
from dawl_profiles.profiles import describe
from dawl_cluster.training_recovery import report_payload


class ContributionFailure(ValueError):
    def __init__(self, job_id, job, reason, detail):
        super().__init__(reason)
        self.job_id, self.job, self.reason, self.detail = job_id, job, reason, detail

    def evidence(self, stage):
        return {'schema': 'hypertrix.training-failure-evidence/1',
                'stage': stage['id'], 'round': stage['round'], 'revision': stage['recovery_revision'],
                'parent': stage['head'], 'job': self.job_id,
                'contribution': self.job['contribution']['transaction'],
                'expected': self.job['contribution']['adapter'],
                'reason': self.reason, 'detail': self.detail}


def verify_contribution(network, job_id, job, reconstruct):
    """Check availability first, then compare an actual reconstructed payload.

    A failed peer sweep is an availability observation, not an accusation. Model
    execution exceptions are deliberately not classified as bad contributions.
    """
    expected = job['contribution']['adapter']
    try:
        network.fetch(expected)
    except (ValueError, OSError, TimeoutError) as error:
        raise ContributionFailure(job_id, job, 'artifact_unavailable',
                                  {'error': str(error)[:512]}) from error
    observed = describe(reconstruct())
    if observed != expected:
        raise ContributionFailure(job_id, job, 'replay_mismatch', {'observed': observed})
    return {'job': job_id, 'update': job['contribution']['transaction'], 'bit_exact': True}


def current_failure(state, stage, failure):
    """Fence completed work against a concurrent rejection, round or cancellation."""
    current = state['training_stages'][stage['id']]
    job = state['training_jobs'][failure.job_id]
    return (current['status'] == 'active' and current['round'] == stage['round']
            and current['recovery_revision'] == stage['recovery_revision']
            and job['contribution'] is not None
            and job['contribution']['transaction'] == failure.job['contribution']['transaction'])


def store_failure(objects, stage, failure):
    artifact = objects.put(canonical_json(failure.evidence(stage)))
    return report_payload(stage, failure.job_id, failure.job, failure.reason, artifact['sha256'])
