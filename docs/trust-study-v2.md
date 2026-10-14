# Fixed-advice pilot v2

Yngvar's 14 October 2026 design replaces multiple-model comparisons for the first
participant prototype. Existing exploratory outputs and the v1 lab remain intact.
Use `/study-admin` for previews and export, `/participant` for pilot collection.
The financial scenarios and candidate good/bad labels remain provisional.

## Experimental controls

- The exact six supplied Norwegian advice strings are frozen in `research/trust-study-v2.json`.
- Advice and provenance have separate JavaScript renderers. No inference API is called.
- A: no documentation; B: development description; C: identical B description plus evidence links.
- Documentation is between participants; scenario order is randomized within participants.
- Each block of 24 assignments contains each of 3 documentation conditions crossed with
  every one of 8 three-scenario quality patterns once, shuffled with system randomness.
  This balances each scenario/quality/condition at block completion; incomplete blocks,
  dropout and small samples can be imbalanced. No causal effect is asserted by the software.
- Researcher-authored advice is identified as such in all conditions. Actual model-development
  evidence is not represented as provenance of the stimulus wording. Review this design choice
  with Yngvar before recruitment; additional experimenter attribution would change the treatment.
- No answer correctness feedback is shown between trials. Trust, confidence and difficulty have
  no default selection. No financial expert approval is inferred from arithmetic tests.

## Storage and data dictionary

Local `artifacts/trust-study-002/study.sqlite3` contains durable assignments, trials and events.
The version is SHA256 over the protocol, exact disclosures, interface source hashes and evidence
file hashes. `<version>.json` preserves that snapshot; evidence files are copied under their
hashes, so changing a live report does not rewrite an earlier participant's disclosure.

Export includes incomplete trials and completed trials; filter explicitly for the analysis.

| Field | Meaning |
| --- | --- |
| session_id | Random 128-bit pseudonym; no name, IP or browser fingerprint stored by this module |
| protocol_sha256 | Frozen protocol, documentation, evidence and interface snapshot |
| allocation | Position in the shuffled block; retain abandoned allocations |
| condition | A/B/C, researcher export only |
| candidate_quality | Proposed good/bad variant, not expert-approved; researcher export only |
| ordinal / case_id | Presented order and stable scenario identifier |
| advice_sha256 / scenario_sha256 | Canonical-JSON hashes of exact text / displayed scenario |
| documentation_sha256 | Hash of the exact visible disclosure including frozen evidence URLs |
| presented / submitted | UTC server timestamps; first presentation persists on refresh |
| elapsed_seconds | Submission minus first presentation, including pauses and browser inactivity |
| response.decision | X/Y or current/refinance, as applicable |
| response.trust / confidence / difficulty | Integers 1-7, individually required |
| response.reliance | follow / check / reject |
| response.reason | Optional text, capped at 1,500 characters |
| events | documentation_open / evidence_open with UTC timestamps; max 100 per trial |
| review_status | Explicit pilot/provisional marker |

Session tokens are random, stored as hashes server-side and kept in browser sessionStorage.
They are not exported. SQLite transactions serialize assignment and submission. An identical
retry returns success without adding a second response; conflicting retries are rejected.
The next trial cannot be submitted before being presented. Server restart preserves sessions.

This is a loopback research pilot. Operator preview and participant routes share a local host;
they are not a hardened blinding or recruitment boundary. Before external participant use,
separate operator authentication, consent/retention/withdrawal policy and study approval must be
agreed by the research team. Nothing in the public Azure showcase writes to this database.

## Before interpreting results

Yngvar reviews scenario validity and plausible error variants; pilot task difficulty, ceiling
effects and documentation comprehension; agree the final condition count, endpoints, sample size,
randomization/attrition handling and analysis plan. The three-condition design here preserves the
existing prototype's A/B/C distinction as an explicit working assumption. Changing wording or design
creates a new snapshot and allocation block, not a silent edit of previous trials.
