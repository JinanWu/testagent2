# GCP dashboard data-flow debugging notes

Use this reference when a deployed dashboard shows surprising values and the stack is Cloud Run + BigQuery + scheduled ETL/snapshot tables.

## Investigation pattern

1. Reproduce from the deployed API, not only the UI.
   - Fetch the dashboard API endpoint that feeds the suspicious widgets.
   - Record any `source`, `run_id`, `run_ts`, date range, row counts, and metric fields returned by the API.
   - If the browser is unavailable, curl the API/HTML endpoints and inspect response size, status, and JSON keys.

2. Confirm which GCP project and service are actually serving the dashboard.
   - Do not trust the local default `gcloud config get-value project`; explicitly pass `--project` on every command.
   - Check Cloud Run web/backend service env vars, image tags, and backend origins.
   - Verify the backend's BigQuery project/dataset env vars match the intended dev/prod environment.

3. Compare source/raw tables against materialized snapshot/reporting tables by period.
   - For each month/week/partition, compute raw row counts, scored row counts, and relevant non-null fields.
   - Compare with snapshot/report rows: `run_id`, `run_ts`, `tour_date_start/end`, `opinion_count`, `scored_count`, top-level metric values.
   - Identify periods where snapshots exist but raw rows do not, or where latest snapshots have zero scored rows.

4. Inspect snapshot selection logic.
   - A dashboard may intentionally avoid empty latest snapshots by selecting latest `scored_count > 0`.
   - This can still select mock/simulated snapshots if they have positive scored counts.
   - Check whether mock/empty/actual data is explicitly modeled (`source_type`, `is_mock`) or inferred from `run_id` naming.

5. Check scheduled jobs and logs.
   - List Cloud Scheduler jobs and Cloud Run jobs/executions for the reporting pipeline.
   - Read recent Cloud Run job logs for date ranges, API row counts, BigQuery row counts, and writes to the snapshot table.
   - Watch for jobs that write empty snapshots when upstream API/BQ returns zero rows.

## Common root causes

- Latest dashboard KPI is sourced from a mock/simulated snapshot because snapshot selection only checks `scored_count > 0`.
- Trend charts mix actual historical data, null empty snapshots, and mock snapshots, causing sudden drops/jumps.
- Scheduled ETL continues writing zero-row snapshots, creating null points in trend data.
- Local gcloud default project is prod while the investigation target is dev; commands without explicit `--project` inspect the wrong environment.

## Recommended fix directions

- Add explicit snapshot metadata such as `source_type = actual|mock|empty`, `is_mock`, `raw_row_count`, `scored_count`, and coverage flags.
- For current KPI, select latest actual non-empty snapshot unless the user explicitly wants mock data as the headline value.
- For trend charts, either exclude mock/empty snapshots or return source metadata so the UI can visually distinguish them.
- In scheduled jobs, skip writing report snapshots when the reporting query returns zero rows, or write them with `source_type='empty'` and have the API exclude them from headline/trend calculations.
