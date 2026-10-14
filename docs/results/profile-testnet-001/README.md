# Profile testnet 001: interrupted performance probe

This first local protocol-3 chain used four fresh CometBFT validators, six real
13-book tokenizer profiles and two CPU auditors. It committed 35 passing v3
receipts before being deliberately stopped to address telemetry overhead. It
did **not** complete the 78 jobs or produce certified profile Blueprints; it must
not be reported as the successful full-corpus cluster run.

Public quorum certificates are retained for the [initial state](before-outage-certificate.json),
[one-validator outage](during-outage-certificate.json) and [interruption](interrupted-certificate.json).
The outage observation advanced from height 168 to 243 and from zero to three
accepted receipts while validator 3 was stopped. That validator was restarted
before the later interruption. No token mismatch was reported at the saved
interruption boundary. The state at that boundary was height 841, with 35 receipts,
zero fully replicated jobs and zero new Blueprint heads.

A native-volume SQLite probe measured 100 separate telemetry commits at
1.119449 seconds and one batch of 100 writes at 0.012223 seconds. This motivated
bounded transfer-metrics batching; signed transactions, the durable outbox and
consensus receipts retain their original persistence paths. This is a narrow
storage probe, not a controlled tokenizer or overall network speed benchmark.

The frozen image used for this interrupted run was
`86bd0443a953031b023ec7be12c22ac504a77133835ffe5cab76df3bf4d9367b`.
The subsequent run uses fresh validator keys and chain identity. It may reuse
public content-addressed corpus artifacts after hash and signature verification;
it does not reuse validator state or audit receipts.
