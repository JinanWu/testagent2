# Passport-recog prod Cloud Run log triage (2026-05-28)

Scope:
- project: `prod-cola-rd`
- service: `passport-recog-data`
- region: `asia-east1`
- revision observed: `passport-recog-data-00015-4c7`

Key findings:
- Cloud Run readiness was healthy: `Ready=True`, 100% traffic on the latest ready revision.
- Recent request logs were all HTTP 200, but application stderr still showed Gemini failures.
- Repeated Gemini upstream errors were `429 RESOURCE_EXHAUSTED` / `Resource exhausted. Please try again later.`
- Empty-field recheck warnings appeared repeatedly and likely amplified request cost and latency.

Useful query pattern:
- Request logs:
  - `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND logName="projects/prod-cola-rd/logs/run.googleapis.com%2Frequests" AND timestamp>="<ISO8601>Z"' --project=prod-cola-rd --format=json`
- App stderr:
  - `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND logName="projects/prod-cola-rd/logs/run.googleapis.com%2Fstderr" AND timestamp>="<ISO8601>Z"' --project=prod-cola-rd --format=json`

What to look for:
- request logs 200 + stderr `429 RESOURCE_EXHAUSTED` = upstream pressure, not a Cloud Run crash
- repeated `結果為空，將重新辨識一次` warnings = internal retry amplification
- request latency tail matters more than average; inspect p95/max and the worst request windows
