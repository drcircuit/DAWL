# Shared study review

The research-team review has its own Azure Storage account, `hypertrixstudy1014`,
in resource group **DAWL**, Norway East. It is separate from the Hypertrix Console.
Neither site links to the other. The new account hosts only the review application
and the five public evidence documents used by the documentation condition.

Review URL: https://hypertrixstudy1014.z1.web.core.windows.net/

Demo URL: https://hypertrixstudy1014.z1.web.core.windows.net/participant.html

This deployment is **review/demo only**. It has no response API, database,
researcher export, model endpoint or access to the laptop. The browser does not
read or serialize the answers: moving to the next task discards the current form.
Refreshing the page resets the demonstration. There is no application analytics,
cookie, local storage or session storage. Azure still handles ordinary web
requests. This is a publicly readable demonstration, not an authenticated
research administration service; `noindex` is a crawler hint, not access control.

## Pages

- `/` or `/index.html`: research-team overview, all 18 previews.
- `/participant.html`: three-question demo, with A/B/C selected before starting.
- `/preview.html?case_id=extra-payment&variant=good&condition=C`: one fixed preview.

The six advice texts and the scenarios are copied without modification from
`research/trust-study-v2.json`. The B/C wording is identical; C adds the evidence
links. The components that render advice and provenance are shared with the local
study. Research-team labels are absent from the task itself. The demo shuffles
tasks and picks advice variants in browser memory; it does not use the study's
balanced allocation or produce research observations.

`research/trust-study-model-strict-002.json` selects the completed, fully replayed
model. Builds verify its pinned release signature, lineage and metadata before
copying evidence. All five disclosure files must exist. Their filenames are their
SHA256 digests. An explicit deployment manifest binds every uploaded file; the
build does not read local participant databases, credentials, or model weights.

## Deploy

```powershell
.venv/Scripts/python.exe scripts/build_study_review.py
.venv/Scripts/python.exe scripts/azure/study_review.py setup
.venv/Scripts/python.exe scripts/azure/study_review.py deploy
```

Setup validates the exact subscription, group, region and resource tag. It uses
an account key only in memory to enable static hosting; deployments use the
signed-in Entra user with Blob Data Contributor scoped to the new `$web` container.
The ordinary anonymous blob-access setting remains disabled. The static website
has its own public HTTPS endpoint. Deployment details are saved under ignored
`artifacts/study-review/deployment.json`.

No VM or container is started. Azure charges for storage, operations and transfer;
static hosting itself has no separate enablement charge. See Microsoft's
[static hosting documentation](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website).

The original local participant collection application and its SQLite data remain
separate. An externally recruited study would need its own agreed protocol,
response backend and researcher access; this review site does not enable that.
