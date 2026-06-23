# BigQuery dashboard summary truncation check

Use this when a dashboard summary appears to stop mid-sentence and the user wants to know whether the front end or BigQuery is responsible.

## Verification sequence

1. Inspect the frontend render path first.
   - Search for hard string operations near summary rendering: `slice(`, `substring(`, `substr(`.
   - Search CSS/layout classes that could visually clip text: `overflow-hidden`, `line-clamp`, `truncate`, fixed height/max-height.
   - Confirm whether the detail/full-text view renders the raw `summary` value directly.

2. Inspect backend mapping.
   - Confirm the BigQuery JSON field used by the API, usually `summary_tree`.
   - Confirm the node lookup path, e.g. `_summary_at_path(summary_tree, ["日本"])`.
   - Check whether backend falls back to `metrics_tree.summary` if `summary_tree` misses a node.

3. Query the exact BigQuery snapshot that the dashboard uses.
   - Prefer the same ordering contract as backend: latest valid snapshot for the period, with non-zero `scored_count` first.
   - Fetch `TO_JSON_STRING(summary_tree)` and `TO_JSON_STRING(metrics_tree)` for that `run_id`.
   - Parse JSON locally and extract the exact node path from `summary_tree.children`.

## Example BigQuery discovery query

```sql
SELECT
  run_id,
  run_ts,
  tour_date_start,
  tour_date_end,
  summary_model,
  JSON_VALUE(metrics_tree, "$.scored_count") AS scored_count,
  JSON_VALUE(metrics_tree, "$.opinion_count") AS opinion_count
FROM `PROJECT.DATASET.opinion_tree_metrics_summary_snapshot`
WHERE tour_date_start >= DATE "YYYY-MM-01"
  AND tour_date_start < DATE_ADD(DATE "YYYY-MM-01", INTERVAL 1 MONTH)
ORDER BY COALESCE(SAFE_CAST(JSON_VALUE(metrics_tree, "$.scored_count") AS INT64), 0) DESC,
         run_ts DESC
LIMIT 10;
```

Then fetch one row by `run_id`:

```sql
SELECT
  run_id,
  CAST(run_ts AS STRING) AS run_ts,
  CAST(tour_date_start AS STRING) AS tour_date_start,
  CAST(tour_date_end AS STRING) AS tour_date_end,
  TO_JSON_STRING(summary_tree) AS summary_tree,
  TO_JSON_STRING(metrics_tree) AS metrics_tree
FROM `PROJECT.DATASET.opinion_tree_metrics_summary_snapshot`
WHERE run_id = "RUN_ID"
LIMIT 1;
```

## Report format

Report:
- project / dataset / table
- run_id, run_ts, period, summary_model
- node path checked
- node opinion_count / scored_count from `metrics_tree`
- summary character length and final 50-100 chars
- full node summary if the user asked to compare with the frontend

## Interpretation

If the stored BigQuery node summary itself ends mid-sentence and the frontend directly renders `ctx.summary` without substring operations, the dashboard is consistent with BigQuery; the truncation happened upstream during summary generation or persistence, not in frontend rendering.

Do not fix frontend layout until raw payload and BigQuery storage have been compared.