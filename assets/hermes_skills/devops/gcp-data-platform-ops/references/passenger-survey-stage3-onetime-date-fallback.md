# Passenger survey Stage3 one-time date fallback repair

Use this reference when a passenger-survey Stage3/dashboard snapshot needs a one-off historical repair for rows missing `tour_date` but still carrying a usable creation timestamp.

## Durable lesson

If a user explicitly accepts approximate recovery because the original travel date is no longer recoverable, keep two concerns separate:

1. Durable ETL/read/export semantics that may be promoted into normal code when requested:
   - Use `tour_date` when present.
   - Else use `FORMAT_DATE('%Y%m%d', DATE(create_time))` as an approximate travel date.
   - If both are missing, exclude the row.
   - Preserve no-code rows under the explicit unclassified branch described below.
   - Add regression tests around SQL/date fallback and tree classification.
2. One-time repair operations that must not be copied into ETL unless explicitly requested as a backfill:
   - BigQuery snapshot backups / `WRITE_TRUNCATE` / table replacement.
   - Full historical rebuild orchestration.
   - Hardcoded corrective windows or temporary audit/helper columns.
3. For actual one-time repairs, make the approximation visible in logs and reports: counts of fallback rows, discarded rows, affected months, backup table, and post-write verification.

## Important pitfall: missing tour_code/tour_name

Rows missing `tour_date` may also be missing classification fields such as `tour_code` and `tour_name`. If the Stage3 tree classifies solely by tour-code prefix, those recovered rows will still be dropped as unmapped unless handled explicitly.

For this accepted one-time approximation, route records with no tour code into an explicit, honest bucket rather than pretending they belong to a product line:

```text
未分類
└── 無團號
    └── 依 create_time 估算
```

Suggested behavior:

- For `tour_code` null/empty rows included by create-time fallback:
  - set display tour code to `無團號`
  - set display tour name to `無團號（依 create_time 估算）`
  - append under the explicit unclassified branch
- For non-empty tour codes that still do not map to a known prefix/tree leaf:
  - keep them in unmapped reporting; do not silently put them into the no-code bucket.

## Verification queries/patterns

Before rebuild:

```sql
SELECT
  COUNT(*) AS total_rows,
  COUNTIF(tour_date IS NULL OR tour_date = '') AS missing_tour_date,
  COUNTIF((tour_date IS NULL OR tour_date = '') AND create_time IS NOT NULL) AS fallback_candidates,
  COUNTIF((tour_date IS NULL OR tour_date = '') AND create_time IS NULL) AS discarded_missing_both
FROM `PROJECT.DATASET.SOURCE_TABLE`;
```

Fallback month distribution:

```sql
SELECT
  FORMAT_DATE('%Y%m', DATE(create_time)) AS create_ym,
  COUNT(*) AS fallback_rows,
  COUNTIF(tour_code IS NULL OR tour_code = '') AS missing_tour_code,
  COUNTIF(tour_name IS NULL OR tour_name = '') AS missing_tour_name
FROM `PROJECT.DATASET.SOURCE_TABLE`
WHERE (tour_date IS NULL OR tour_date = '')
  AND create_time IS NOT NULL
GROUP BY create_ym
ORDER BY create_ym;
```

Effective date range:

```sql
SELECT
  MIN(effective_tour_date) AS min_effective_tour_date,
  MAX(effective_tour_date) AS max_effective_tour_date,
  COUNTIF(effective_tour_date IS NOT NULL) AS usable_rows,
  COUNTIF((tour_date IS NULL OR tour_date = '') AND create_time IS NOT NULL) AS fallback_rows,
  COUNTIF(effective_tour_date IS NULL) AS discarded_rows
FROM (
  SELECT
    tour_date,
    create_time,
    COALESCE(NULLIF(tour_date, ''), FORMAT_DATE('%Y%m%d', DATE(create_time))) AS effective_tour_date
  FROM `PROJECT.DATASET.SOURCE_TABLE`
);
```

After rebuild:

- Snapshot row count equals full effective month count.
- `MIN(tour_date_start)` / `MAX(tour_date_end)` cover the expected historical range.
- `SUM(JSON_VALUE(metrics_tree,'$.opinion_count'))` increases by the recovered classified/unclassified rows.
- Recent affected months now have non-null `head_weighted_mean` when fallback rows have usable sentiment scores.
- The explicit unclassified branch appears in `metrics_tree.children.未分類.children.無團號.children.依 create_time 估算` with the expected counts.
- Known fixed product/tour-code paths still exist and keep their expected counts/scores.

## Reporting shape

Report the repair as an approximation, not a normal data truth:

- “This is a one-time fallback because original travel dates are unrecoverable.”
- “Rows with no `tour_date` use `DATE(create_time)` as approximate travel date.”
- “Rows missing both dates were excluded.”
- “Rows also missing tour code are included under `未分類 / 無團號 / 依 create_time 估算`.”
- Include counts for total source rows, fallback rows, discarded rows, final snapshot rows, affected months, and a fixed-path verification.
