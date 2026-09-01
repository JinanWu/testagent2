# Passenger Survey dashboard Stage 3 verification

Use this when checking whether ETL Stage 3 produced dashboard-ready BigQuery snapshots without writing data.

## What Stage 3 does
- Reads the formal source table (usually `project_semantic_features`).
- Groups rows by `tour_code` into a fixed-depth organization tree.
- Computes `metrics_tree` with:
  - `opinion_count`
  - `scored_count`
  - `head_weighted_mean`
  - `level_weighted_mean`
- Builds `summary_tree` via Gemini (`gemini-2.5-flash` by default).
- Appends one row per month to `opinion_tree_metrics_summary_snapshot`.

## BigQuery target schema
`dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
- `run_id` STRING REQUIRED
- `run_ts` TIMESTAMP REQUIRED
- `source_project` STRING REQUIRED
- `source_dataset` STRING REQUIRED
- `source_table` STRING REQUIRED
- `tour_date_start` DATE NULLABLE
- `tour_date_end` DATE NULLABLE
- `summary_model` STRING NULLABLE
- `metrics_tree` JSON REQUIRED
- `summary_tree` JSON REQUIRED

## Read-only checks that were useful
```sql
SELECT COUNT(*) AS total_rows,
       COUNTIF(JSON_VALUE(metrics_tree, '$.opinion_count') IS NOT NULL) AS rows_with_metrics,
       COUNTIF(JSON_VALUE(summary_tree, '$.summary') IS NOT NULL AND JSON_VALUE(summary_tree, '$.summary') != '') AS rows_with_root_summary
FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`;
```

```sql
SELECT FORMAT_DATE('%Y-%m', tour_date_start) AS ym,
       JSON_VALUE(metrics_tree, '$.opinion_count') AS opinions,
       JSON_VALUE(metrics_tree, '$.head_weighted_mean') AS head_mean,
       JSON_VALUE(metrics_tree, '$.level_weighted_mean') AS level_mean
FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
ORDER BY run_ts DESC
LIMIT 6;
```

## Important observation from this session
- Stage 3 can produce a valid metrics snapshot and a valid root summary for the latest month.
- Historical monthly rows may intentionally contain empty `summary_tree.summary` placeholders even when `metrics_tree` is populated.
- In the current dev snapshot, 41 rows existed and only the latest month had a non-empty root summary.

## Pitfall
- Do not assume an empty `summary_tree.summary` means Stage 3 failed.
- Always inspect `metrics_tree` first, then check whether the summary generation path is intentionally disabled, cached, or only partially rerun.
- If you need to compare months, group by `FORMAT_DATE('%Y-%m', tour_date_start)` rather than relying on `run_ts` alone.
