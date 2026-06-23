# Passenger-survey dashboard trend API debugging

Use this when a local dashboard shows a message like「歷史趨勢暫時載入失敗」or the trend chart is empty while the main hierarchy/KPI cards load.

## Fast triage

1. Verify the frontend API paths separately through the Vite proxy and direct backend:
   - `/dashboard/api/v1/satisfaction/hierarchy`
   - `/dashboard/api/v1/satisfaction/trend?node_type=root&node_id=root`
2. If hierarchy is 200 but trend is 404, the failure is not the chart rendering layer; the active backend lacks or does not expose the lazy trend endpoint.
3. Inspect the hierarchy payload source block and trend arrays:
   - `source.runId`, `source.runTs`, `tourDateStart`, `tourDateEnd`
   - `trend.monthly.length`, `trend.yearly.length`
4. Compare that source run_id/month against BigQuery latest rows in:
   - `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
   - `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`
5. Check the active backend process before blaming data:
   - `ps -p <pid> -o pid,lstart,etime,command`
   - `lsof -p <pid> | awk '$4=="cwd" {print $9}'`
   Existing port-8000 processes may come from an old worktree or even `.Trash`; do not assume they match the frontend task folder.

## BigQuery checks

Snapshot table aggregate:

```sql
SELECT
  COUNT(*) AS row_count,
  MIN(tour_date_start) AS min_tour_date_start,
  MAX(tour_date_end) AS max_tour_date_end,
  COUNT(DISTINCT run_id) AS run_count,
  MAX(run_ts) AS latest_run_ts
FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`;
```

Monthly latest snapshots and usable trend values:

```sql
WITH ranked AS (
  SELECT
    FORMAT_DATE('%Y-%m', tour_date_start) AS month,
    run_id, run_ts, summary_model, tour_date_start, tour_date_end,
    SAFE_CAST(JSON_VALUE(metrics_tree, '$.level_weighted_mean') AS FLOAT64) * 100 AS passenger,
    SAFE_CAST(JSON_VALUE(metrics_tree, '$.head_weighted_mean') AS FLOAT64) * 100 AS route,
    SAFE_CAST(JSON_VALUE(metrics_tree, '$.opinion_count') AS INT64) AS opinion_count,
    SAFE_CAST(JSON_VALUE(metrics_tree, '$.scored_count') AS INT64) AS scored_count,
    ROW_NUMBER() OVER (PARTITION BY FORMAT_DATE('%Y-%m', tour_date_start) ORDER BY run_ts DESC) AS rn
  FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
  WHERE summary_model != 'metrics-only-validation-stub'
)
SELECT month, run_ts, summary_model, ROUND(passenger, 1) AS passenger, ROUND(route, 1) AS route, opinion_count, scored_count
FROM ranked
WHERE rn = 1
ORDER BY month DESC;
```

Source feature table monthly coverage:

```sql
SELECT
  FORMAT_DATE('%Y-%m', PARSE_DATE('%Y%m%d', NULLIF(tour_date, ''))) AS month,
  COUNT(*) AS row_count,
  COUNTIF(ai_sentiment_score IS NOT NULL) AS scored_rows,
  COUNT(DISTINCT tour_code) AS tour_codes,
  COUNT(DISTINCT tour_name) AS tour_names
FROM `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`
WHERE SAFE.PARSE_DATE('%Y%m%d', NULLIF(tour_date, '')) IS NOT NULL
GROUP BY month
ORDER BY month DESC;
```

## Important JSON-shape pitfall

The formal snapshot `metrics_tree` is a raw recursive tree. Root metrics are not necessarily under `$.metrics.passenger` / `$.metrics.route`.
For the current passenger-survey Stage3 shape, root-level weighted means are commonly:

- `$.level_weighted_mean` → passenger-style mean before scaling by 100
- `$.head_weighted_mean` → route/head-style mean before scaling by 100
- `$.opinion_count`
- `$.scored_count`
- `$.children`

If `JSON_VALUE(metrics_tree, '$.metrics.passenger')` is null, inspect `TO_JSON_STRING(metrics_tree)` or `JSON_KEYS(metrics_tree)` before concluding the snapshot is corrupt.

## Interpreting outcomes

- Hierarchy 200 + trend endpoint 404 + BigQuery has monthly snapshots:
  - Backend/API contract issue. Add or restore `/dashboard/api/v1/satisfaction/trend` and derive monthly series from latest snapshot rows.
- Hierarchy source run_id is older than BigQuery latest:
  - Backend may be filtering `summary_model` too narrowly, using a cache, or running from an old process/worktree.
- BigQuery latest snapshot exists but is `metrics-only-validation-stub`:
  - Exclude stub rows from semantic charting unless intentionally previewing metrics-only validation.
- Trend arrays empty but no endpoint error:
  - Treat as “暫無歷史趨勢資料” in UI rather than “載入失敗” unless the request actually failed.

## Reporting shape

Keep the report operational:
- API status: hierarchy status, trend status, hierarchy source run_id/month, trend lengths.
- BigQuery status: snapshot row count/run count/latest run, feature row count/latest ingest, latest 2-3 monthly trend rows.
- Runtime status: backend PID, command, cwd, start time, whether it matches the intended repo.
- Root cause category: data missing vs API endpoint missing vs stale backend vs frontend fallback wording.
