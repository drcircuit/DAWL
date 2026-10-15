# Governed training recovery

Implemented and activated 15 October 2026 on a fresh protocol-6 testnet.
[Live trial 003](results/training-recovery-003/README.md) recovered actual GPU
training from a withheld submitted update and a missing verifier. Seven GPU
contributions, ten bit-exact replay checks and two certified checkpoints are
archived with the complete signed history. The historical protocol-4 demo,
implementation and certificates remain unchanged.

## Contract

Protocol 6 adds recovery to a new `hypertrix.oracle-training-stage/3` under an
existing collaboration manifest. Stages using schemas 1 and 2 retain their
original transitions and quorum rules. Activation requires drained stages and
the same implementation digest as the collaboration execution contract. Use a
fresh deployment pinned to this build for the first recovery pilot; do not send
the activation to validators running the archived implementation.

The immutable stage includes:

```json
"recovery": {
  "round_timeout_blocks": 300,
  "max_rejections": 3,
  "supersedes": null
}
```

The timeout is measured in committed block heights, starts at stage registration
and resets only after a verified round. Renewing leases, reporting failures and
rejecting updates cannot extend it. A timeout makes cancellation eligible; it
does not automatically cancel the stage or declare misconduct. Consensus itself
must make progress for a block-based deadline to advance.

```mermaid
flowchart TD
  T[Trainer submits committed update] --> V[Named verifiers fetch and replay]
  V -->|All matching votes| C[Promote checkpoint]
  V -->|Unavailable artifact or replay mismatch| F[Signed failure report and evidence object]
  F --> O[Collaboration owner reviews report]
  O --> R[Reject update, archive votes, increment revision]
  R --> L[New trainer lease and generation]
  L --> V
  V -->|Missing verifier until deadline| X[Owner cancels stage]
  V -->|Conflicting verifier votes| D[Disputed stage]
  D --> X
  X --> P[Keep last verified checkpoint]
  P --> N[Register replacement stage and explicit verifier roster]
  N --> T
```

### Failed contribution

1. A named verifier publishes a content-addressed evidence object and sends
   `report_training_failure`. The transaction binds the stage, round, recovery
   revision, job, exact contribution transaction, reason and evidence digest.
2. An unresolved report for current work blocks checkpoint promotion. Reports
   are observations: unavailable data may reflect a partition, and a mismatch
   may reflect an execution error. They are not slashing evidence.
3. The collaboration owner, already the authorized stage publisher, can send
   `reject_training_update` referencing that report. The discarded submission
   and existing votes remain in recovery history. The round revision increases,
   and the shard becomes claimable by another admitted trainer.
4. The rejected trainer cannot reclaim that job. A new lease generation fences
   the old submission. Previously cast votes are cleared, and new candidates
   commit the new revision. Every named verifier must vote again.

Reports are bounded and deduplicated per signer/contribution. The rejection
budget is stage-wide, fixed in advance, and cannot be reset by reassignments.
Neither a reporter nor the owner can manufacture a promoted checkpoint through
recovery commands. A malicious reporter or owner can still deny service; this
is supervised recovery inside the existing admitted collaboration model.

### Missing verifier or disputed reduction

After the round deadline, the owner may send `cancel_training_stage` with reason
`round_timeout`; a disputed stage can be cancelled immediately with reason
`disputed`. Cancellation clears outstanding leases and releases the branch's
active-stage slot. It retains all contributions, votes, reports and earlier
checkpoints. Late results and votes against that stage are rejected.

A replacement stage explicitly names the new verifier set and references the
cancelled stage in `recovery.supersedes`. It extends the branch's last verified
head and adapter. Replacement verifiers must already be invited, admitted and
accepted into the same execution cohort. There are still at least two distinct
verifiers, and every verifier named in the new stage must approve. No existing
roster or checkpoint certificate is edited.

The executable manifest must match the replacement stage's initial adapter,
round count and work catalog. Recreate that manifest and its content hash for
continuation after partial progress. Worker configurations must explicitly point
to the replacement stage. Automatic stage following and automated replacement
selection remain deployment work.

## Operator interface

Run with `src` on `PYTHONPATH`, from the owner node's environment. The root must
contain its existing `cluster.json`, genesis/plan pins and, for mutations, its
private signing key. Commands require certificate-verified state reads.

```console
python -m dawl_training.recovery_cli status --root NODE_ROOT --stage STAGE_ID
python -m dawl_training.recovery_cli reject --root OWNER_ROOT --stage STAGE_ID --report REPORT_TX_ID
python -m dawl_training.recovery_cli cancel --root OWNER_ROOT --stage STAGE_ID
```

From the repository root, use `python scripts/training_recovery.py` with the same
arguments. This entry point puts the source packages ahead of the repository's
legacy command wrappers on the Python import path.

Activation is the existing publisher's signed `activate_training_recovery`
command with `{version: 6, implementation_sha256: BUILD_DIGEST}`. Register the
new schema-3 stage through `register_training_stage`. The owner CLI deliberately
does not select new participants or rewrite manifests on its own.

Workers now record missing/corrupt submitted artifacts and replay mismatches as
failure receipts in opted-in stages. Runtime exceptions remain runtime errors;
they are not attributed to trainers. Missing parent/model/input artifacts,
bootstrap discovery failures and invalid aggregate bounds can still require
operator intervention and eventual cancellation. Cancelled workers keep serving
their stored artifacts until stopped.

## Validation and live deployment

`tests/test_training_recovery.py` covers rejected-update reassignment, retained
evidence, stale votes/reports/results, unauthorized recovery, immutable rosters,
full replacement quorum, wrong-cohort admission, cancelled leases, prior-head
preservation, disputed cancellation, fixed budgets and deadline progression.
Its HTTP test serves corrupt bytes, withholds an object, and supplies a healthy
fallback; corrupt content never enters the local object store. These tests use
synthetic adapter bytes, not simulated claims of GPU training.

The regression suite also replays the archived protocol-4 transaction history
and requires byte-identical final state.

The live trial used four fresh CometBFT validators, two source auditors, three
trainers and three admitted verifier identities (one intentionally absent).
All 13 books received both source audits. A trainer was stopped after submitting
its GPU update. Signed availability failures blocked promotion; owner rejection
allowed another trainer to acquire the shard. Its adapter matched the withheld
commitment exactly, and both verifiers approved the recovered checkpoint.

A second stage received one replay vote while its other verifier remained
absent. After the committed 300-block deadline, cancellation preserved the
verified head. A late signed vote was rejected. A new stage explicitly named a
replacement roster, repeated the work and earned both required replay votes.

Offline verification replayed **160 signed transactions over 1,952 blocks** and
reproduced the certified state exactly. Eight numerical replay checks support
the two promoted checkpoints; two more belong to the cancelled attempt. With
all trial GPU workers stopped, a fresh HTTP client retrieved the final adapter
from the Cortex and matched its committed hash.

This establishes supervised recovery with real workers on one host and GPU.
Automated replacement selection, continuing availability challenges and trials
across independent operators remain future work. See the linked archive for
genesis pins, frozen source, stage manifests, failures and numerical receipts.

## Repeat the trial

`scripts/recovery_pilot.sh` prepares new identities and separate containers.
It never resets an existing trial. The fixed recovery listener addresses must
be free before another run; stop the completed recovery deployment using its
explicit run ID, leaving the historical testnet untouched.

```sh
HYPERTRIX_RECOVERY_RUN=003 sh scripts/recovery_pilot.sh stop
HYPERTRIX_RECOVERY_RUN=004 sh scripts/recovery_pilot.sh build
HYPERTRIX_RECOVERY_RUN=004 sh scripts/recovery_pilot.sh prepare
HYPERTRIX_RECOVERY_RUN=004 timeout --signal=TERM --kill-after=60 2400 sh scripts/recovery_pilot.sh trial
```

Run inside the configured WSL/Podman environment. Preparation uses the retained
public model and corpus objects from `training-001` and `profile-v3-002`, and
checks the discovery/provider route allowlists. Workers preserve their DHT
sequence across stage-specific stores. The driver stops trial GPU workers on
completion or failure; validator and provider state is retained. Evidence is
written to `/var/lib/dawl/training-recovery-RUN/evidence`.
