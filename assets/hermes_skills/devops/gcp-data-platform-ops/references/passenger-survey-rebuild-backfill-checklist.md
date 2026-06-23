# Passenger-survey style BigQuery rebuild/backfill checklist

Use this as a concrete reference when a BigQuery dashboard rebuild combines historical rows, API-supplemented rows, local model inference, vector search consensus, and long-running local drivers.

## 1. Treat each derived field family separately

Verify each family with aggregate counts before and after writes; do not infer completion from one field family.

Typical checks:

- Keys: `COUNT(*)`, distinct `(appoint_no, opinion_no)`, duplicate key groups, null key rows.
- Text/source: nonempty `suggestion_describe`, `rebuild_source` distribution.
- Embeddings/UMAP: `ARRAY_LENGTH(embedding_vector)` distribution, missing `x/y`.
- AI labels: all `ai_*_mark` fields have no NULL when the target is fully inferred.
- Sentiment: `ai_sentiment_label` and `ai_sentiment_score` missing count, score range.
- AI consensus: `ai_consensus_score`, `ai_winner_labels`, `ai_winner_support` missing count, support/score range.
- HM fields separately: HM labels may legitimately be unavailable; HM consensus can remain missing even if AI fields are complete.
- Quality flags: after a backfill, stale flags such as `needs_embedding`, `needs_umap`, `needs_sentiment`, `needs_ai_labels`, `needs_consensus` can remain and should be reviewed/cleared if downstream uses them.
- Outlier fields: verify allowed domain, not just non-null count; if expected binary, count values outside `0/1`.

## 2. Model-inference backfill: verify feature lineage first

Before running local classifier inference from a stored vector column:

1. Query `ARRAY_LENGTH(embedding_vector)` for target rows.
2. Inspect model config/input dimension.
3. Run a 1–2 row local smoke test through the exact model.
4. If dimensions differ, regenerate the classifier-specific representation for inference only; do not overwrite dashboard embeddings unless explicitly asked.

Example lesson: a dashboard table may store 3072-dim embeddings for visualization/search while an older classifier expects 768-dim multilingual embeddings. In that case, regenerate 768-dim features from text for the classifier and still leave the dashboard `embedding_vector` intact.

## 3. Long local backfills on macOS

When the driver runs on a user's MacBook, prevent idle sleep for the expected duration plus buffer:

```bash
caffeinate -dimsu -t 10800
pmset -g assertions | egrep -i 'caffeinate|PreventUserIdle|PreventSystemSleep|PreventDiskIdle'
```

Report the caffeinate PID and expiration time. Use background process tracking if available.

## 4. Consensus after labels

If consensus depends on labels, run in this order:

1. Backfill labels into per-worker stage tables.
2. UNION per-worker stages.
3. MERGE labels into the main table.
4. Recompute consensus for rows that were previously label-missing.
5. Verify `stage_rows = missing_query_rows`, duplicate stage keys = 0, and remaining labeled consensus gaps = 0.

Do not call the rebuild complete until the dependent consensus pass is finished.

## 5. HM unavailable normalization

If the user confirms HM/manual labels will never arrive for the missing rows and downstream dashboards need non-null values, normalize unavailable HM fields instead of leaving NULLs:

- `hm_*_mark`: `COALESCE(field, FALSE)`
- `hm_consensus_score`: `COALESCE(field, 0)`
- `hm_winner_support`: `COALESCE(field, 0)`
- `hm_winner_labels`: if NULL or empty, set to `['無標籤']`

Create a backup of the affected HM fields before this destructive normalization. Make clear that this is a business decision to encode “no HM label available” as false/zero, not a recovered human label.

## 6. Clean final table before replacing a production table

For rebuilds that introduced audit/workflow columns, create a clean final table before swapping into production:

1. Start from the fully verified rebuild table.
2. Drop temporary rebuild-tracking fields such as `rebuild_source`, `rebuild_loaded_at`, and `rebuild_quality_flags` unless downstream explicitly needs them.
3. Keep intentional new business fields such as `tour_name`.
4. Verify final schema is “original columns + approved new columns” and no unexpected extras.
5. Verify key coverage against the original table:
   - original keys missing in final = 0
   - new keys added = expected supplement count
   - duplicate keys = 0
6. Verify all required derived families again on the clean final table.
7. Note BigQuery `CREATE TABLE AS SELECT` may change `REQUIRED` key fields to `NULLABLE`; if downstream enforces schema mode strictly, create the replacement with explicit schema instead of relying on CTAS.
8. Before production replacement: backup original table, replace from clean final, then rerun the same completeness/schema checks on the production table.

## 7. Final report shape

Keep the final report aggregate-only and explicit:

- total rows
- duplicate key groups
- missing counts for each derived family
- score range checks
- stage vs main counts
- quality flags that remain and whether they are stale
- metadata gaps that may be source limitations rather than failed backfills
- schema deltas versus the original table, including intentional added columns and CTAS mode changes
- key coverage versus the original table
- 2–3 sample rows only if the user asks for examples
