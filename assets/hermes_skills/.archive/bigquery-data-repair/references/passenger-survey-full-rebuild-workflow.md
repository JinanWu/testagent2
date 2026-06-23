# Passenger survey full rebuild / backfill workflow

Use this reference when rebuilding the passenger survey row-level table from a processed BigQuery base plus historical CSV archives and current-year API extracts.

## Stage semantics in the repo

The dashboard jobs repo uses three pipeline stages:

1. Stage 1 (`ai-label` new API)
   - Calls `/report/customer-feedback/ai-label`.
   - Builds/merges row-level records into BigQuery.
   - Handles AI labels, nickname replacement, embeddings, UMAP x/y.
   - HM fields are written as NULL; KNN/consensus fields remain NULL.
   - Existing matched rows with HM already filled should not be overwritten.

2. Stage 2 (`label-analyze` old API)
   - Calls `/report/customer-feedback/label-analyze`.
   - Looks up existing BigQuery rows by `(appoint_no, opinion_no)`.
   - Does not create missing rows; missing keys are skipped.
   - Fills HM labels only where all HM fields are still NULL.
   - Uses existing `embedding_vector` to compute KNN/consensus and updates HM/derived fields.

3. Stage 3 (report/snapshot)
   - Reads the same row-level BigQuery table over a `tour_date` window.
   - Maps rows to the organizational tree by `tour_code` prefix.
   - Builds metrics tree (`opinion_count`, `scored_count`, `head_weighted_mean`, `level_weighted_mean`).
   - Uses Gemini to summarize leaf→branch→root and appends a snapshot table.
   - Does not call the label APIs.

## Safe rebuild pattern

1. Classify table roles first.
   - Use schema and scalar completeness checks to identify the full row-level base table.
   - Do not infer a helper/backfill table is the base just because it is large.
   - Avoid `SELECT *` and avoid downloading `embedding_vector` during reconnaissance.

2. Inventory encrypted historical archives safely.
   - Use official `7zz` (for example via `py7zz`) to list/test encrypted 7z files.
   - If listing fails with password/header errors, stop for corrected password.
   - After extraction, normalize historical CSVs into staging keys with `(appoint_no, opinion_no)`, `tour_yyyymm`, flags for non-empty suggestion/tour fields, and raw date.

3. Measure historical coverage before writing.
   - Load normalized keys to a staging table.
   - Compare historical rows to the chosen base on `(appoint_no, opinion_no)`.
   - Separate blank-suggestion rows from non-empty `suggestionDescribe` rows; blank rows may have been intentionally excluded from semantic tables.

4. Create a new rebuild table, never overwrite the original source table.
   - Copy the chosen base table BigQuery-side to avoid local embedding downloads.
   - Add provenance/audit fields such as `rebuild_source`, `rebuild_loaded_at`, `rebuild_quality_flags`, and optional missing display fields like `tour_name`.
   - Insert historical non-empty missing rows only after coverage is quantified.
   - Preserve blank-suggestion missing rows in an audit table instead of silently dropping them.

5. Flag incomplete appended rows.
   - Historical CSV rows usually lack model-derived fields: embeddings, UMAP, AI/HM labels, sentiment, consensus.
   - Use explicit flags like `needs_embedding`, `needs_umap`, `needs_ai_labels`, `needs_hm_labels`, `needs_sentiment`, `needs_consensus` so downstream runs know what remains incomplete.

6. For current-year API supplementation, stage old/new APIs separately then combine by key.
   - New API may have AI labels and some tour fields; old API may have HM labels.
   - Stage both extracts as BigQuery tables.
   - Combine to one row per `(appoint_no, opinion_no)` with `COALESCE` preference for non-empty values.
   - Update existing rebuild rows for newly available display fields (for example `tour_name`) before inserting missing keys.
   - Insert missing API keys into the rebuild table with provenance `api_*` and quality flags.

7. Verify after every write.
   - Total rows, distinct composite keys, duplicate key rows.
   - Inserted rows by source/provenance.
   - API coverage after supplementation: each staged API source/month should have `missing_rebuild_rows = 0` if the goal is full API coverage.
   - Audit action counts by year/source.

## Reporting expectations

Keep user-facing output concise and operational:
- Explain stage1/stage2/stage3 in terms of source, action, and output.
- Report exact table names and row counts.
- State clearly that original tables were not overwritten.
- Stop for confirmation before running expensive downstream model recomputation or making the rebuild table the production source.
