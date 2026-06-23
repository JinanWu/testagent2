# GCP dashboard snapshot mismatch: dev multi-agent-web / multi-agent-service

Session note:
- User observed `https://dev-multi-agent-web.colatour.org/dashboard` showing old data compared with BigQuery.
- Cloud Run services were Ready and returned HTTP 200.
- The dashboard API `/dashboard/api/v1/satisfaction/hierarchy` returned `runId=simulated-monthly-2026-04` and matched the 2026-04 snapshot in BigQuery.
- The latest BigQuery snapshot for 2026-05 existed but was incomplete for dashboard scoring:
  - `opinion_count=6548`
  - `scored_count=0`
  - `head_weighted_mean=null`
  - `level_weighted_mean=null`
- Raw source data for 2026-05 existed in `project_semantic_features`, so the issue was not missing input rows.

Takeaway:
- In dashboard investigations, do not assume "latest snapshot" is the correct serving candidate.
- Compare the serving API's `run_id` / `run_ts` / metric fields against the latest BigQuery snapshot and check whether the latest row is actually usable.
- A non-empty snapshot with zero scored rows can cause the service to fall back to the last valid simulated snapshot.

Useful probes:
- Cloud Run service health, env vars, and backend origin
- API response metadata (`runId`, `runTs`, counts, root metrics)
- BigQuery latest snapshot row and historical snapshot rows
- Raw/source row counts by period, especially scored vs non-scored rows