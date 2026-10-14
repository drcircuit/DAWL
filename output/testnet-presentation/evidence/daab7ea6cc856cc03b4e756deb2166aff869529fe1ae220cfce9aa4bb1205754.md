# Swarm validation — 13 October 2026

The five-container swarm recovered from injected corruption, withholding, provider churn and a replica partition. Eight real-corpus books passed sampled CPU replay; an independently replayed false approval of a deliberately invalid vector commitment was contradicted. The original platform dataset and its 36-record ledger were unchanged.

This validates a configured, admitted swarm on **one physical host**. It does not establish Internet-scale performance, independent operators, proof of another node's work, open admission or training lineage. The existing GPU-produced input-embedding commitments were inputs to this experiment; new GPU scheduling was not exercised.

## Deployment and evidence

Successful cold lab: `artifacts/swarm-lab-002`. Exercise output: `artifacts/swarm-run-004`. Public archived evidence: [results/swarm-run-001](results/swarm-run-001/). The five independently running nodes are two provider/replicators, one dedicated CPU auditor, one dedicated ledger replicator and one intentionally dishonest auditor. Each container has its own identity and state; provider stores are separate copies. CPU and memory limits are 2 cores and 2 GiB per container.

The inputs are the eight-book corpus already pinned in the platform run: 5,850,515 source bytes, 1,438 source chunks, 1,657,163 tokens and a shared 154,389,504-byte GPT-2 input embedding table. Swarm checks sampled **16 chunks per valid book**, not all 1,438 chunks. Source and snapshot integrity were checked in full. The canary has an additional, intentionally false signed vector entry in the lab copy only.

| Observation | Result |
|---|---|
| All providers withhold | Auditor reports unavailable, not transform failure |
| Corrupt provider plus honest alternative | 249 corrupt pieces rejected at the recorded final checkpoint |
| Honest provider stops during table download | Partial object never published; unavailable receipt produced |
| Honest provider restarts | Transfer resumes; 26 persisted piece-cache hits recorded |
| Valid source/token/vector paths | Eight original audits pass, 16 sampled chunks per book |
| Dummy approval of invalid committed vectors | Independent CPU replay disagrees; one disputed claim |
| All freeloader claims rechecked | Nine rechecks complete: eight pass and one invalid |
| Replicator partition heals | All five nodes converge on 37 lineage records and 40 receipts |
| Unexpected local errors | Zero `error` receipts |
| Original dataset | Its prior 36-record checkpoint is unchanged |
| Automated tests | 84 tests pass; frontend JavaScript syntax check passes |

The final 40 receipts comprise 9 dummy passes, 16 honest passes (8 original audits and 8 rechecks), 2 invalid results (original canary audit and recheck) and 13 availability failures. Signed dummy passes remain attributed claims. They were not promoted into the platform audit ledger, dataset assembly or reward eligibility.

The interruption was observed at 49.733 seconds after the exercise began. The condition of eight valid book passes plus a disputed false approval was met at 344.715 seconds. An intermediate 39-receipt convergence check passed at 372.733 seconds; one final recheck completed afterward. `final-deployment.json` separately verifies all 9 original jobs and 9 rechecks completed and all five nodes converged on the final 40 receipts. The reusable exercise now waits for all rechecks before its convergence check.

These durations include deliberate outages, retries, corpus replay and the Windows/WSL filesystem. They are not throughput or scaling benchmarks. At the final deployment snapshot the auditor counted 340,337,314 received application bytes, including metadata, corrupt deliveries and retries; 251 pieces downloaded; 101 complete objects published; and 187 object-cache hits. No dense sequence-vector files were transferred. There was no packet capture, so this is not a wire-byte measurement.

## Public replay and reproducibility

Two separate public verifier runs use fresh caches and no private key: one replays the false canary approval and one replays a valid claim. Their JSON reports are archived alongside the signed observations. The canary replay detects a reconstructed-vector mismatch at sampled chunk 3 and reports `matches_claim=false`; its expected CLI exit status is 1.

Runtime image: `255bbca83db7402d47812541661ab83dfa633125d535f201a40717bbab32a047`. Host and container implementation digests agree: `331965bf0b0a8c4b7b06a89cb87b378c00dc2f4e17341117665b54b632620f70`. Exact container dependency versions are recorded in `runtime.json`. Image content, public membership, admission certificates, lineage, observation envelopes, phase snapshots and file hashes are retained; private keys and full model/corpus artifacts are not committed to this results directory.

Lineage set digest: `b16ac521d5ba1a7dd252fa4b01e7fed582df24f3b223211db21e27cf47b9d1eb`. Final receipt-set digest: `18ab26862bd33b7c417f1ac26e82f8a31cdd769e460d49676fd2bc9471b4815d`. Swarm policy pin: `80de79ff0a30ac6cd73138b9e7333efb88a8d208e58476646a69ddd98c05febe`.

Earlier development attempts exposed Windows/WSL fault-file replacement failures, excessive repeated receipt-history validation, and a runner startup race. Fixes use immutable fault versions, incremental gossip cursors, cached artifact descriptors, retained job schedules and bounded observer retries. The successful cold run was prepared separately after those fixes; earlier logs are retained as development failures, not successful trials.

The observatory at `http://127.0.0.1:8880/` exposes the live topology, artifact availability matrix, receipts and disputes. HTTP/API assertions and JavaScript syntax were checked. Browser visual inspection was not performed because browser automation was unavailable in this session.

See [swarm.md](swarm.md) for protocol, startup, public replay commands and remaining limits. The next scaling experiment should control corpus size, peer count and link conditions, measuring verified throughput, CPU/RAM, storage amplification and recovery separately from intentional fault waiting.
