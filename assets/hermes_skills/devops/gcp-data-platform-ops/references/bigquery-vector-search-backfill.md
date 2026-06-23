# BigQuery VECTOR_SEARCH backfill pattern

Use this when repairing/backfilling consensus or nearest-neighbor fields with BigQuery `VECTOR_SEARCH` over a large table.

## Pattern

1. Confirm target table, key columns, and exact rows that are eligible for repair.
2. Snapshot only the rows/columns that may be updated into a backup table before any merge.
3. Materialize a small query table containing only the missing/eligible rows instead of searching all rows against all rows.
4. If a single `VECTOR_SEARCH` still fails with memory pressure or query timeout, shard the query table by a deterministic hash, for example:

```sql
WHERE MOD(ABS(FARM_FINGERPRINT(CONCAT(CAST(appoint_no AS STRING), '#', CAST(opinion_no AS STRING)))), 32) = chunk_id
```

5. For each chunk:
   - create/replace the chunk query table
   - run `VECTOR_SEARCH(base_table, embedding_col, chunk_table, top_k => k+1, ...)`
   - exclude self-matches
   - `QUALIFY ROW_NUMBER() ... <= k`
   - append results into a stage table
6. After all chunks finish, merge the stage table back to the main table.
7. Verify:
   - backup row count equals initial eligible missing count
   - stage row count equals expected eligible missing count
   - post-merge eligible missing count is zero
   - remaining missing rows are explainable, e.g. missing source labels rather than failed consensus computation

## Why this helps

A single large `VECTOR_SEARCH` can fail with BigQuery memory errors dominated by JOIN operations even when the query side is much smaller than the base side. Deterministic chunking reduces peak join memory while preserving full base-table search coverage for each query row.

## Pitfalls

- Do not shard the base search table unless the business logic accepts neighbors from only a subset of the corpus. Usually shard only the query/missing rows.
- Keep chunking deterministic so reruns are resumable and audit-friendly.
- Do not treat a created stage table as proof of success; verify row count and merge results.
- BigQuery scripts may finish child statements while the parent script still runs; inspect job status and stage row growth when monitoring long runs.
