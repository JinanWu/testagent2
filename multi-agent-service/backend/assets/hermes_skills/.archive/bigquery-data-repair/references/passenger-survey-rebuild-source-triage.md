# Passenger survey rebuild: source triage and safe sequencing

Context:
- Project/data class: passenger survey row-level rebuild feeding both processed row data and later tree/report outputs.
- Main BigQuery dataset: `dev-cola-rd.passenger_survey_pred_dashboard`.
- Critical key: `(appoint_no, opinion_no)`.

Confirmed source roles from the rebuild session:
- `project_semantic_features` is the full row-level base candidate.
  - It has row text, tour fields, AI/HM labels, sentiment fields, embedding/UMAP, and consensus/outlier fields.
  - In the observed run it had 449,636 rows, 449,636 distinct keys, no duplicate keys, and no null keys.
- `project_semantic_features__sentiment_backfill_20260514_042548` is a sentiment helper/source table, not a row-level base.
  - It only has `appoint_no`, `opinion_no`, `ai_sentiment_label`, `ai_sentiment_score`.
  - In the observed run it was a subset of the main table: 409,536 overlap rows, 0 helper-only rows, 40,100 main-only rows.
- Do not choose a base table by row count alone. Distinguish full row-level schema from helper/backfill schema.

Safe sequencing for this class of rebuild:
1. Start with read-only inventory: auth, dataset tables, schemas, archive presence, local repo presence.
2. Compare candidate BQ tables with scalar columns only; never `SELECT *` from tables containing `embedding_vector`.
3. Use `COUNT(DISTINCT CONCAT(CAST(appoint_no AS STRING), '#', CAST(opinion_no AS STRING)))` for distinct composite key counts; BigQuery rejects `COUNT(DISTINCT STRUCT(...))` in this context.
4. Decide source roles:
   - full row-level base table
   - helper/backfill tables
   - report/snapshot tables
5. Only after source roles are verified, inspect historical archives and APIs for missing coverage.
6. Write repaired/rebuilt output to a new table only; do not mutate the existing base table.

Historical archive pitfall:
- The 2023/2024/2025 historical archives were encrypted 7z files with encrypted headers.
- `bsdtar` cannot inspect encrypted 7z headers.
- Python `py7zr` may fail with `Bad7zFile` when the password is wrong or unsupported.
- A useful setup workaround is installing `py7zz` and using its bundled official `7zz` binary to test/list archives.
- If official `7zz` reports `Cannot open encrypted archive. Wrong password? / Headers Error`, stop and ask for a corrected password; do not attempt partial extraction or guess encodings.

Reporting pattern:
- Save raw query outputs under `reports/01_*.json` and a concise markdown interpretation such as `reports/01_bq_candidate_table_comparison.md`.
- For long rebuilds, maintain phase reports (`00_inventory.md`, `01_bq_candidate_table_comparison.md`, `02_historical_archive_inventory_status.md`, etc.) so the user can recover context after interruptions.

Sentiment nuance:
- If recent rows show blank/0 sentiment while older historical rows have sentiment, compare helper-source overlap before assuming upstream failure.
- In the observed dataset, 2026-04 and 2026-05 rows in the main table had zero non-empty sentiment rows, while the helper table covered only older overlapping keys.
