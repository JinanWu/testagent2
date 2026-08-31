# Dashboard metrics-null-root verification

Use this when a dashboard/API returns an older snapshot or seems to ignore the newest BigQuery row.

## Symptom pattern
- UI/API is healthy (200 OK), but the displayed month is stale.
- Latest snapshot row exists in BigQuery.
- `summary_tree` still contains non-empty summaries.
- `metrics_tree` root fields are `null` and/or `scored_count = 0`.
- The backend may keep rendering the last valid scored snapshot.

## What to check
1. Confirm the exact API response feeding the UI.
   - Record `runId`, `runTs`, root metric values, and the route path.
2. Query the snapshot table directly.
   - Compare latest row vs last valid row.
   - Check `opinion_count`, `scored_count`, and root `head_weighted_mean` / `level_weighted_mean`.
3. Inspect the raw/source table for the same date range.
   - Verify there are rows and whether per-row scoring fields are populated.
4. Compare `summary_tree` and `metrics_tree`.
   - A non-empty `summary_tree` does not imply the metrics tree is usable.
5. Find the fallback rule.
   - If the code selects "latest valid" / "latest scored" data, the newest snapshot may be skipped intentionally.

## Concrete example (May 2026 mood dashboard)
- Latest snapshot row existed for May 2026, but root metrics were null:
  - `opinion_count=6548`
  - `scored_count=0`
  - root `head_weighted_mean=null`
  - root `level_weighted_mean=null`
- `summary_tree` still had non-empty summaries at many nodes.
- The raw source table had rows for May 2026, but the aggregate snapshot had not become serving-ready.

## Useful probes
- `SELECT run_id, run_ts, TO_JSON_STRING(summary_tree), TO_JSON_STRING(metrics_tree) ... ORDER BY run_ts DESC LIMIT 1`
- `SELECT COUNT(*), COUNTIF(score IS NOT NULL) ... WHERE date BETWEEN ...`
- Drill down into root and a few leaves to see whether the issue is global or subtree-specific.

## Interpretation
- If the summary exists but metrics are null, the root cause is usually upstream scoring / aggregation, not the dashboard UI.
- If the latest row is unusable, the serving layer may fall back to an older valid snapshot by design.
- Do not patch the frontend to hide the symptom until you know whether the snapshot writer or selector is wrong.
