# BigQuery vector search and ML backfill notes

Use this reference when repairing or backfilling BigQuery dashboard tables that need vector-search-derived fields or offline ML inference fields.

## Vector-search consensus backfills

When `VECTOR_SEARCH` over a large base table fails with memory/timeout errors, avoid retrying the same full query. A safer pattern is:

1. Snapshot the target rows and original columns before mutation.
2. Materialize a small query/missing table containing only rows that need the derived field.
3. Split the query/missing table into deterministic chunks, e.g. `MOD(ABS(FARM_FINGERPRINT(CONCAT(key1, '#', key2))), N)`.
4. For each chunk, run `VECTOR_SEARCH(base_table, query_chunk, top_k => k+1)` and write results into a stage table.
5. Prefer chunk-specific stage tables if running chunks in parallel; `UNION ALL` them later into the final stage table.
6. Do one final `MERGE` into the main table after validating stage row counts.

Trade-off: serial chunks are slower but safer for memory/slot pressure. Parallel chunks can reduce wall time, but use per-chunk stage tables and a small concurrency cap to avoid simultaneous writes to the same table and BigQuery resource spikes.

## Local or Cloud Run ML sentiment backfills

If a deployed API does multiple tasks but the repair only needs sentiment fields, avoid calling the full API if it would trigger unrelated embedding/classification/Pub/Sub side effects. Instead, reuse the underlying sentiment model directly in a batch script.

Recommended shape:

1. Create a missing table with primary keys and text fields only.
2. Create a stage table with primary keys, derived sentiment columns, `computed_at`, and `worker_id`.
3. Run asynchronous workers over deterministic chunk ranges.
4. Batch model inference inside each worker rather than one row at a time.
5. Append worker results to either per-worker stage tables or a shared append-only stage table.
6. Merge stage results into the main table and verify missing counts, stage row count, and a small sample.

For MacBook/local runs, warn that sleep/offline state may interrupt the driver process. For durable asynchronous work, prefer Cloud Run Jobs or another cloud-side runner with explicit parallelism.

## Progress reporting for long-running backfills

When the user asks for status during a running repair/backfill, report from live evidence rather than only process uptime:

- Poll tracked local/background processes and check child process CPU where relevant.
- Read the latest log lines for completed chunk/worker milestones and errors.
- Query BigQuery stage/missing tables for exact counts; distinguish worker-stage rows, final stage rows, and main-table MERGE status.
- Include current chunk/worker position, processed/total counts, percentage, ETA, process/job IDs, and whether anything appears stuck or failed.
- Explicitly state when results are only staged and have not yet been MERGEd into the main table.

## Verification checklist

- Backup table exists and row count matches intended update set.
- Missing/query table row count matches the preflight gap count.
- Stage table row count equals expected processed rows before MERGE.
- Remaining missing counts distinguish rows that are truly unprocessable from rows that failed backfill.
- Use aggregate checks and 2-3 spot examples; avoid downloading large tables.
