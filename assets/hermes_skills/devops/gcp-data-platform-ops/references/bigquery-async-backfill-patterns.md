# BigQuery async backfill patterns

Use these patterns when repairing or enriching a BigQuery table with expensive derived fields such as VECTOR_SEARCH consensus scores or local/ML model predictions.

## Expensive BigQuery VECTOR_SEARCH backfills

Observed durable pattern:

- Full-table or large missing-set VECTOR_SEARCH can fail with BigQuery memory pressure even when row count looks moderate.
- Error shape to recognize: `Resources exceeded during query execution`, with JOIN operations as the dominant memory consumer.
- Safer pattern:
  1. Create a backup table of only keys and columns to be updated.
  2. Create a missing-query table containing only rows eligible for the derived fields.
  3. Split missing rows by deterministic shard:
     `MOD(ABS(FARM_FINGERPRINT(CONCAT(CAST(key1 AS STRING), '#', CAST(key2 AS STRING)))), N)`
  4. Run VECTOR_SEARCH per shard.
  5. Append each shard result to a stage table, or write per-worker stage tables and UNION ALL later.
  6. MERGE stage back to the main table.
  7. Verify stage row count, remaining missing rows, and bad/null derived fields.

For conservative runs, process shards serially in a background job. For faster runs, process shards in parallel only if each worker writes its own stage table, then UNION ALL before a single final MERGE. This avoids concurrent append/MERGE contention and gives a clean retry boundary per shard.

## Local async ML inference backfills

When a deployed service does extra side effects or unnecessary work, do not call it just to reuse one model output. Prefer a direct batch script if the model can be loaded locally or in a batch runtime.

Recommended pattern:

1. Create a BigQuery backup table of the target rows and original derived columns.
2. Create a missing table with keys, input text/features, and deterministic `worker_id` shard.
3. Start N local worker processes in the background.
4. Each worker:
   - loads the model once;
   - reads only its `worker_id` shard;
   - performs batched inference;
   - writes to its own stage table, e.g. `_stage_w00`, `_stage_w01`.
5. After all workers finish, create the total stage table with `UNION ALL`.
6. Perform one MERGE back to the main table.
7. Verify:
   - backup rows vs missing rows;
   - sum of worker stage rows vs missing rows;
   - remaining missing target fields;
   - bad/null stage rows;
   - small spot-check sample.

## Practical notes

- Use background processes with completion notification for long-running local orchestrators.
- Keep logs per orchestrator and per worker.
- On portable laptops, warn that sleep/offline can stop the local orchestrator even if an already-submitted BigQuery job may finish.
- If durability matters, move the same pattern to Cloud Run Jobs / Cloud Batch / Cloud Shell rather than relying on a laptop.
- For sentiment-style text inference, batch size and worker count should be measured with a small sample before the full run; short text often benefits from modest batch sizes and multiple CPU workers.
