# Passenger survey dashboard backfill / rerun checklist

Use this as a concrete reference when debugging or backfilling the passenger-survey dashboard ETL / BigQuery snapshot flow.

## Key lessons

- Treat source API environment and target BigQuery environment as separate axes. It can be valid to read from production API and write to dev BigQuery, but only after making that cross-environment routing explicit and getting user approval.
- Before deleting or rerunning anything, do a read-only source probe by month/day and record counts from each relevant upstream endpoint:
  - `ai-label` for Stage 1 row data / AI labels.
  - `label-analyze` for Stage 2 HM labels.
- Query the target BigQuery tables before destructive work:
  - raw/features table counts by `tour_date` year/month.
  - snapshot/report table counts by `tour_date_start`, `tour_date_end`, `run_id`, and `run_ts`.
- Snapshot tables can contain simulated or stale positive-count rows that are selected by “latest non-empty” dashboard logic. Look for `run_id` patterns such as `simulated-*` and date ranges that do not match raw table availability.
- If the dashboard only displays the latest month of text summaries, do not rerun expensive historical summary generation unless the user explicitly asks. Backfill metrics/row data for history, and generate summaries only for the latest visible month.
- For destructive cleanup, separate raw-row deletion from snapshot deletion. It is common for raw rows for a target year to be zero while snapshot rows still need cleanup.

## Safe read-only probes

Example source API monthly probe shape:

```python
import calendar, requests
base = "https://<host>/report/customer-feedback"
for endpoint in ["ai-label", "label-analyze"]:
    for m in range(1, 13):
        last = calendar.monthrange(2026, m)[1]
        params = {
            "startDateTime": f"2026-{m:02d}-01T00:00:00",
            "endDateTime": f"2026-{m:02d}-{last:02d}T23:59:59",
        }
        r = requests.get(f"{base}/{endpoint}", params=params, timeout=240)
        r.raise_for_status()
        print(endpoint, m, len(r.json().get("data", [])))
```

Example target BigQuery probes:

```sql
SELECT SUBSTR(CAST(tour_date AS STRING), 1, 6) AS yyyymm, COUNT(*) AS row_count
FROM `<project>.<dataset>.project_semantic_features`
WHERE SAFE_CAST(SUBSTR(CAST(tour_date AS STRING), 1, 4) AS INT64) = 2026
GROUP BY yyyymm
ORDER BY yyyymm;

SELECT tour_date_start, tour_date_end, run_id, run_ts,
       JSON_VALUE(metrics_tree, "$.opinion_count") AS opinion_count
FROM `<project>.<dataset>.opinion_tree_metrics_summary_snapshot`
WHERE tour_date_start >= DATE "2026-01-01"
   OR STARTS_WITH(run_id, "simulated-monthly-2026-")
ORDER BY tour_date_start, run_ts DESC;
```

## Destructive cleanup pattern

Do not execute this until the target project/dataset/table and scope have been verified and the user has approved destructive work.

```sql
DELETE FROM `<project>.<dataset>.project_semantic_features`
WHERE SAFE_CAST(SUBSTR(CAST(tour_date AS STRING), 1, 4) AS INT64) = 2026;

DELETE FROM `<project>.<dataset>.opinion_tree_metrics_summary_snapshot`
WHERE tour_date_start >= DATE "2026-01-01"
   OR STARTS_WITH(run_id, "simulated-monthly-2026-");
```

## Rerun sequencing

1. Stage 1 by month for months where `ai-label` has rows.
2. Stage 2 by month for months where `label-analyze` has rows and Stage 1 has already populated matching keys.
3. Stage 3 metrics/snapshot for the latest visible month only unless asked otherwise.
4. Verify raw row counts, snapshot `opinion_count`, latest snapshot selection, and dashboard API response/source metadata.
