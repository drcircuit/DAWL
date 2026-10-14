# Collaboration and branch metadata rehearsal 001

This is a **synthetic protocol exercise**, not a new GPU training result or a
CometBFT deployment. It runs real Ed25519-signed transactions through the same
deterministic application state machine used by the Oracle. Adapter descriptors,
runtime labels and the small input corpus are test fixtures.

The transcript contains 81 accepted transactions and two rejected attempts.
It creates one homogeneous branch, then two descendants of its checkpoint under a
heterogeneous manifest with two declared execution environments. Both descendants
have distinct adapter hashes and independent checkpoint heads. The homogeneous
branch's head remains unchanged.

Three explicit consumer selections resolve successfully. A sibling checkpoint
substituted under another branch ID is rejected. The transcript also demonstrates
rejection of an ungoverned legacy stage and an unsupported epsilon-verification
policy. The separate adversarial unit tests cover further invalid cases.

| File | Purpose |
|---|---|
| `plan.json` | Synthetic initial chain plan and public actor keys |
| `transactions.json` | Signed transactions, logical heights and expected policy outcomes |
| `state.json` | Final state to compare against deterministic replay |
| `summary.json` | Participation manifests and branch graph |
| `selections.json`, `resolved.json` | Explicit consumer pins and resulting artifact references |
| `verification.json` | Replay counts, exact final-state hash and scope |
| `implementation.json` | Source digest used to generate the rehearsal |
| `gpu-environment.json`, `cpu-environment.json` | Subsequently measured local environments, separate from the synthetic transcript |
| `validation.json` | Test counts, CLI/fingerprint checks and observed live baseline status |

No private keys are exported. The synthetic environment's `implementation_sha256`
is a fixture; `implementation.json` identifies the actual generating source.
The transcript has no validator quorum certificate. Its exact replay establishes
policy behavior and signature validity, not tensor execution, hardware identity,
distributed availability, generalization quality or trust in unknown operators.

The subsequent local fingerprint check captured the NVIDIA RTX 5090 Laptop GPU
and AMD Ryzen AI 9 HX 370 CPU from the existing training runtime. Both CLI modes
produced manifest drafts; homogeneous mode rejected a draft containing both
environments. These are environment measurements, not a CPU/GPU training
comparison. The full suite passed (217 tests, five skipped), followed by the
expanded 12-test collaboration suite and nine training-runtime checks. The
protocol-4 archive replayed to the exact same state, and all 11 running baseline
containers remained online. Protocol 5 was not activated on that chain.

```sh
python scripts/rehearse_collaboration.py --output docs/results/collaboration-rehearsal-001 --verify
# Produce an independent rehearsal with fresh ephemeral keys in a new directory:
python scripts/rehearse_collaboration.py --output output/my-collaboration-rehearsal
```

See [collaboration modes](../../collaboration_modes.md) for the architecture,
equations, security boundaries and operator workflow.
