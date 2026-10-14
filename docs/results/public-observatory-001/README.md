# Azure read replica and publication-link split

Executed 14 October 2026. The public observatory uses Norway East Azure Storage
static hosting, with local computation and no running cloud container group.
Private signing-state archives were preserved before stopping the older cloud
pilot. The newer local training chain has a separate genesis and remains active.

The local publisher was paused at 16:37:33 UTC. The public browser retained the
last accepted height **20,044**, with header time 16:35:27 UTC, and displayed
**LAST ACCEPTED SNAPSHOT · UPDATES PAUSED** after its 180-second freshness limit.
After restarting the publisher, the cloud accepted height **20,371**; the local
quorum was still valid and had reached height **20,382** when inspected at
16:40:18 UTC. This exercises interruption of the publication link, not a split
of the validator quorum or a distributed training speedup test.

The downloaded certificate passed the independent Go verifier against the pinned
genesis. A copy with one modified state field was rejected because its bytes no
longer matched the signed header. Neither the invalid candidate nor raw host
telemetry was published. Freshness, rollback and same-height conflict rejection
also have isolated unit tests.

Files:

- `netsplit-test.json`: interruption and recovery observations.
- `snapshot.json`: accepted projection downloaded from Azure.
- `accepted-certificate.json`: exact quorum certificate for that projection.
- `certificate-check.json`: independent acceptance and tamper rejection.

The UI offers certificate downloads; browser rendering trusts the HTTPS service
and does not itself run the cryptographic checker. See
[publication design](../../public_observatory.md) for boundaries and operations.
