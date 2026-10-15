# Seed III: calibration and portable lab evidence

This is a frozen evidence snapshot from 15 October 2026. The full 6,000-update
training phase was still running when it was exported. Final model quality and
three-machine GPU deployment results are not part of this snapshot.

| Check | Observed result | Evidence |
|---|---|---|
| Full corpus reconstruction | All 162,497 documents retokenized; arrays matched | [data verification](data-verification.json) |
| Corpus identity | 183,742,710 train / 14,685,177 validation / 11,779,388 test targets | [Blueprint](dataset/blueprint.json) |
| Compiled 125.9M GPU model | 25,494 synthetic tokens/s, microbatch 32 | [benchmark](compile-batch32/benchmark.json) |
| Fresh-process GPU resume and replay | Updates 10–20: 164/164 tensors and optimizer exact | [replay](probe/replay-0000010-0000020.json), [signed lineage](probe/lineage.jsonl) |
| Artifact/initialization verification | Three checkpoint files, seven signed records; initialization regenerated | [verification](probe/artifact-verification.json) |
| Transfer of real data/model package | 4,440,400,883 bytes; 40 files verified by receiver | [transfer](calibration-transfer.json), [verification](calibration-package-verification.json) |
| Transfer interruption and provider loss | Partial download resumed; replica served original signed package | [integration fixture](integration-004/integration.json) |
| Distributed backpropagation | Two real CPU Gloo ranks; final model digests agree | [integration fixture](integration-004/integration.json) |
| Public-package replay | Separate verifier key, read-only package, trainer private key absent; CPU tensors and optimizer exact | [signed receipt](isolated-auditor/result/receipt.json) |
| Portable runtime | Export imported into empty container storage; real GPU forward/backward doctor passed | [doctor](import-check/doctor.json) |

The public replay and DDP integration tests use a small synthetic fixture. The GPU
calibration and 4.44 GB transfer use the real Seed III model and tokenized corpus.
They are separate experiments with different scopes.

The extracted release's PowerShell `Audit -Cpu` command was also exercised on the
public fixture: 20/20 tensors and AdamW state matched. The packaged source and
4,582,221,862-byte OCI archive have checksums in [release.json](release.json).
The copied Windows archive hash matched. Eight model/data/transport tests and six
console/accounting tests passed.

## Main phase and completion procedure

The [frozen recipe](run/recipe.json) schedules 6,000 updates, global batch 96 and
compiled microbatch 32. The [schedule summary](run/schedule.json) records
432,302,192 target presentations and 170,864,438 unique targets. The best checkpoint
is selected by mixture-weighted validation loss.

The running pipeline then performs a 250-update checkpoint replay, verifies
artifacts, evaluates the selected checkpoint on fixed text/prompts, and publishes
it. A second process waits for successful completion, publishes replay boundaries,
downloads the public package, verifies it, and runs an isolated GPU replay with its
own key. That finalization is configured, not counted as completed evidence here.

Outputs live outside the synced repository under `/var/lib/dawl/seed-lab-001`:

* `run/summary.json`: selected model, test loss and measured training resources.
* `quality-seed2.json`, `quality-seed3.json`: identical raw prompts and held-out snippets.
* `public-finalization-status.json`: waiting, publishing, replaying, complete or failed.
* `public-finalization.json`, `completed-run.md`: consolidated results after acceptance.

The studio at <http://127.0.0.1:8887/> serves the best available validated checkpoint
on CPU while GPU training continues. The Console/TUI show the trainer and separate
node roles. Idle-sleep guards remain active through training and final replay.

## Verification scope

The source hashes, Blueprint, frozen implementation, schedule, signed checkpoint
chain and replay interval are inspectable. Exact replay is scoped to the recorded
hardware/runtime cohort. The full-model lab chain is distinct from the existing
Oracle-certified adapter-training chain. DDP is synchronous and stores a full
model per GPU. The 4090 and 6000 Ada have not yet joined this deployment.

See [architecture and methodology](../../home_training_lab.md), the
[Windows/WSL2 guide](../../../lab/README.md), and the [export manifest](evidence.json).
