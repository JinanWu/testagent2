# BigQuery staged rebuild / repair verification

Use this reference when a BigQuery repair/backfill is implemented as several staging tables or scripts and the user asks what each stage did, what is complete, or what remains.

## Durable pattern

1. Reconstruct the stage graph before reporting.
   - Identify stage table names, final target table, and source tables.
   - Read the SQL scripts or job text when available; do not infer stage meaning from names alone.
   - For multi-statement BigQuery jobs, inspect job metadata/errorResult. `DONE` can still mean failed when `errorResult` is present.

2. Verify with aggregate-only queries for large tables.
   - Prefer `COUNT(*)`, `COUNT(DISTINCT key)`, `COUNTIF(...)`, grouped source counts, and null/empty checks.
   - Avoid pulling large tables locally or printing huge sample dumps.
   - Use only 2-3 representative sample rows if examples are needed.

3. Treat helper/stage tables as intermediate evidence, not success.
   - A stage table with zero rows after a failed job is strong evidence that its downstream merge did not complete.
   - A successful stage create does not prove the final merge/update happened; verify target columns after the merge.
   - Record whether each missing column is blocked by prerequisite data, e.g. consensus cannot be computed until label columns exist.

4. Report by stage and by remaining defect class.
   - For each stage: purpose, input, output/target columns, verification result, and remaining gaps.
   - Separate “completed”, “blocked”, and “needs rerun” instead of mixing them in a single narrative.
   - When quality flags remain stale after data is repaired, call out flag cleanup as a separate finalization task.

## Useful aggregate checks

- total rows vs distinct business keys
- missing embedding/vector counts
- missing UMAP coordinate counts
- missing label counts by source
- missing consensus/support/winner columns by source
- missing sentiment label/score counts by source
- unavailable human-label flags vs actual human-label NULLs
- quality flag distribution after repair

## BigQuery job failure nuance

For BigQuery jobs, `status.state = DONE` is terminal state only. Always check `status.errorResult` and `status.errors`. A timed-out multi-statement script may leave later stage tables empty or unmerged; verify each expected output explicitly.
