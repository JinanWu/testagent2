# BigQuery ML backfill: feature-dimension and staged verification notes

Use when a BigQuery historical backfill loads a local/batch ML model and writes derived fields back to a dashboard table.

## Durable lessons

- Do not assume a column named `embedding_vector` is compatible with the model. Verify the vector dimension against the model input before launching the full run.
  - Example aggregate check: `SELECT ARRAY_LENGTH(embedding_vector) AS dim, COUNT(*) AS row_count ... GROUP BY dim`.
  - Example local smoke test: fetch 1-2 candidate rows, build the model feature matrix, and run `predict_batch` before starting workers.
- If the dashboard embedding dimension differs from the classifier input dimension, identify the exact embedding model used during classifier training and regenerate only the feature representation needed for that classifier. Keep the dashboard embedding unchanged unless the task explicitly asks to rewrite it.
- For local ML inference backfills, use per-worker stage tables and a final `UNION ALL` + single `MERGE` into the main table. Avoid concurrent MERGEs to the main table.
- When a long-running local process is stopped and relaunched with corrected logic, report the old process exit separately from the active replacement process so the user does not mistake an intentional `SIGTERM`/`-15` for failure.

## Verification pattern

Before launch:
- Count target missing rows and confirm keys.
- Confirm backup table row count equals missing-row scope.
- Confirm model input shape with a tiny sample.

During run:
- Track process id/session id.
- Track per-worker stage counts and total stage percentage.
- Scan logs for `ERROR`, `Traceback`, and `failed`.
- Include whether data is still in stage or has been MERGEd back to the main table.

After run:
- Verify final stage rows equal missing rows.
- Verify duplicate key count in stage is 0.
- Verify main-table remaining missing derived fields is 0.
- For dependent derived fields, immediately identify and start/plan the downstream recomputation if requested (for example, AI label backfill followed by AI consensus backfill).
