# Passenger survey Stage3 history-preserving snapshot rebuild

Use when replacing or backfilling `opinion_tree_metrics_summary_snapshot` or similar dashboard tree snapshot tables.

## Durable lesson

A `WRITE_TRUNCATE` rebuild that only generates the latest preview window can make historical trend charts disappear even when source history is intact. Before replacing the formal snapshot table, derive the rebuild month/window set from the formal source table rather than hardcoding recent months.

## Safe workflow

1. Inspect source history and scoring readiness:

```sql
SELECT
  COUNT(*) AS total_rows,
  MIN(tour_date) AS min_tour_date,
  MAX(tour_date) AS max_tour_date,
  COUNTIF(tour_date IS NULL OR tour_date = '') AS missing_tour_date,
  COUNTIF(LOWER(ai_sentiment_label) IN ('positive','negative') AND ai_sentiment_score IS NOT NULL) AS scored_rows
FROM `PROJECT.DATASET.project_semantic_features`;
```

2. Inspect current snapshot history before writing:

```sql
SELECT
  COUNT(*) AS snapshot_rows,
  MIN(tour_date_start) AS min_start,
  MAX(tour_date_end) AS max_end,
  COUNT(DISTINCT FORMAT_DATE('%Y-%m', tour_date_start)) AS distinct_start_months
FROM `PROJECT.DATASET.opinion_tree_metrics_summary_snapshot`;
```

3. Back up the exact production table about to be replaced. If a prior replacement already happened, also back up that intermediate state before the next overwrite.

4. Generate month boundaries from `MIN(tour_date)` / `MAX(tour_date)` in the formal source table. Include empty months inside the range if the dashboard expects continuous monthly trends; those months should have `opinion_count=0` and null score.

5. Rebuild every month/window in that range, not only the current preview month.

6. When loading Python rows into BigQuery JSON columns with `load_table_from_json`, pass nested dict/list objects for JSON fields, not pre-serialized JSON strings. Otherwise BigQuery stores JSON strings and `JSON_VALUE(metrics_tree, '$.opinion_count')` returns null.

7. Replace the formal table only after validating the collected row count equals the expected month count.

8. Post-write verification:

```sql
SELECT
  COUNT(*) AS snapshot_rows,
  MIN(tour_date_start) AS min_start,
  MAX(tour_date_end) AS max_end,
  COUNTIF(JSON_VALUE(metrics_tree,'$.head_weighted_mean') IS NOT NULL) AS months_with_score,
  SUM(CAST(JSON_VALUE(metrics_tree,'$.opinion_count') AS INT64)) AS mapped_opinions
FROM `PROJECT.DATASET.opinion_tree_metrics_summary_snapshot`;
```

Also spot-check representative historical months and one known nested tree path used by the frontend.

## Reporting checklist

- Explain whether missing history is from missing source data, missing scoring fields, or a snapshot overwrite/windowing issue.
- Report source date range and scored row count.
- Report previous snapshot row count/range and new snapshot row count/range.
- Distinguish empty months from failed calculations.
- Name all backup tables created before destructive replacement.
