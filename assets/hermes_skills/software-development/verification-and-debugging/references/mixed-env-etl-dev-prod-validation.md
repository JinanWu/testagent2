# Mixed-env ETL: dev sink with prod-shaped/source data validation

Session pattern captured from a passenger-survey / mood-index ETL validation run.

## Durable workflow

1. Make routing explicit before writes:
   - Source environment/API class, e.g. `--api-env production`.
   - Sink project/dataset/table, e.g. `dev-cola-rd.dataset.table`.
   - Quota/model project overrides when relevant, e.g. `GOOGLE_CLOUD_QUOTA_PROJECT` and `EMBEDDING_PROJECT_ID`.
2. Backfill or write through a staging table, then `MERGE` only the intended columns. Report:
   - generated rows
   - affected rows
   - label/category distribution
   - staging cleanup result
3. Verify the live dev table after the job, not just local logs:
   - total rows in the target date window
   - rows with newly-written fields
   - rows with prerequisite vectors/features
   - min/max effective date used by the pipeline
4. Verify downstream report/snapshot tables separately:
   - recent run IDs/timestamps
   - source table
   - date range
   - key metric fields from JSON columns when the schema stores report payloads as JSON
5. If a later stage computes metrics successfully but hangs during LLM/Gemini summary generation:
   - treat the metrics path and summary path as separate failure domains
   - stop repeated full reruns unless new instrumentation was added
   - add or use a `--skip-summary` / metrics-only mode to prove report writes
   - add per-node summary progress logs and per-call timeout before retrying complete summary mode

## Evidence to collect in the final report

- Target env/project/dataset/table.
- Source env/API class and whether prod was read-only.
- Input/output/affected row counts.
- Error/skipped/unmapped counts.
- Key metrics such as scored_count and weighted means.
- Whether the report row is a formal summary or a metrics-only validation stub.
- Process exit semantics: exit `-15` means SIGTERM/external termination, not a Python exception by itself.

## BigQuery query patterns

When table schemas vary, first inspect the table schema; do not assume column names like `embedding`. In the observed table, vector data was stored as repeated `embedding_vector`.

For report snapshot tables that store metrics in JSON columns, extract fields with `JSON_VALUE(metrics_tree, '$.field')` or `JSON_VALUE(summary_tree, '$.field')` rather than assuming flattened columns exist.
