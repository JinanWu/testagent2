# Cloud Run dashboard verification checklist

Session-derived pattern for dashboard data mismatch investigations.

## What to verify first
- `gcloud auth list --filter=status:ACTIVE` to confirm the active account.
- `gcloud config list` to see the default project; do not assume it matches the target env.
- `gcloud run services list --project=<dev|prod>` and `gcloud run services describe <service> --region=<region>` to verify Ready/traffic/image/env.
- `gcloud logging read` for the exact Cloud Run revision and request path.

## Web/API checks
- If the site is behind Cloudflare or browser automation is blocked, use `curl -ksS -A 'Mozilla/5.0'` against the page and API endpoints.
- Fetch the dashboard JS bundle and inspect it for the real API base path and endpoints; dashboards often mount under `/dashboard` but call `/dashboard/api/v1/...`.
- Record `runId`, `runTs`, and the metric payload returned by the API.

## BigQuery checks
- Query the snapshot table in the same project the service uses, with explicit `--project_id` and `WHERE DATE(run_ts)=...` when comparing a specific day.
- Compare the latest snapshot metadata (`run_id`, `run_ts`, `source_project`, `source_dataset`, `source_table`, `tour_date_start`, `tour_date_end`) against the API response.
- When JSON is embedded in a table column, use `JSON_VALUE(...)` / `TO_JSON_STRING(...)` to inspect the source fields.

## Common root causes
- Cloud Run is healthy, but the dashboard API still serves an older simulated/latest snapshot.
- The frontend is pointed at the right backend, but the backend reads from a stale or mis-selected snapshot row.
- The dashboard page and the API both return 200, so logs alone do not prove the data is current.

## Evidence bundle to collect
- Cloud Run service list/describe output.
- Dashboard API response with source metadata.
- Latest BigQuery snapshot row(s) and their source metadata.
- Cloud Run request logs for the exact endpoint.

## Session note
This was validated on the multi-agent dashboard stack:
- frontend: `multi-agent-web`
- backend: `multi-agent-service`
- API path: `/dashboard/api/v1/satisfaction/hierarchy`
- snapshot table: `passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
