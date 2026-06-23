# Passenger survey fixed-depth Stage3 tree rebuild and production replacement

Use when the dashboard tree must be repaired directly in BigQuery instead of temporarily changing backend/frontend logic.

## Durable pattern

1. Confirm live auth and exact resources first.
   - `gcloud auth list`, `gcloud config get-value project`, `bq version`
   - Confirm target table schema/location and current row count.
2. Back up the formal snapshot table before any truncate/replace.
   - Example target: `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
   - Backup shape: `opinion_tree_metrics_summary_snapshot_backup_YYYYMMDD_HHMMSS`
   - Verify backup count equals original count before proceeding.
3. Rebuild Stage3 from the formal dashboard source table, not helper/stale artifacts.
   - Example source: `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`
4. For front-end drilldown compatibility, emit a fixed hierarchy:
   - `region -> line -> group -> product/tour_name -> tour_code`
   - Do not stop at `group -> tour_code`; that leaves the product-name level missing.
5. Ensure Stage3 source SELECT includes `tour_name`.
   - If `tour_name` is absent from the Stage3 row fetch, the rebuild silently falls back to leaf/group names and verification for a product path will fail.
6. If summaries are not being regenerated, monkey-patch/bypass only summary generation and write an API-compatible empty summary tree:
   - `{"summary": "", "children": {}}`
   - Keep metrics generation intact.
7. When loading rows into BigQuery JSON columns with `load_table_from_json`, pass JSON/dict objects for JSON fields, not pre-serialized JSON strings.
   - Pre-serialized strings can load but make `JSON_VALUE(metrics_tree, '$.opinion_count')` return NULL because the JSON column contains a JSON string rather than an object.
8. Replace the formal table with `WRITE_TRUNCATE` only after local structural validation passes.
9. Verify after replacement using aggregate checks and one path drilldown.

## Verification checklist

- Formal table row count after replacement matches intended snapshot count.
- Monthly/root metrics are extractable with `JSON_VALUE(metrics_tree, '$.opinion_count')` and `JSON_VALUE(metrics_tree, '$.scored_count')`.
- Latest month contains at least one concrete drilldown path, for example:
  - `美加紐澳 -> 紐澳組 -> 三城全覽 -> <tour_name> -> <tour_code>`
- Leaf node has `kind='leaf'`, positive `opinion_count`, and non-null scoring metrics when expected.
- Backup table still exists and has the original row count.

## Pitfalls captured

- A replacement script can appear successful (`output_rows` written) while JSON fields are wrong if dicts were converted to strings before `load_table_from_json`.
- The fixed-depth tree depends on source query columns; changing normalization alone is insufficient if `tour_name` is not fetched.
- User preference for this class of repair: when recomputation is cheap, prefer generating the correct production tree and backing up/replacing the BigQuery snapshot over temporary backend hacks that must later be reverted.
