# Passenger-survey Stage2 no-write verification notes

Session-specific notes for verifying Stage2 BigQuery readiness without mutating data.

## Scope
- Dev project: `dev-cola-rd`
- Dataset: `passenger_survey_pred_dashboard`
- Constraint: no BigQuery writes; only `bq ls`, `bq show --schema`, and `bq query SELECT ...`

## What was verified
- `project_semantic_features` schema matches the Stage2 serializer's update-only field set.
- `serialize_row_stage2()` and `_build_stage2_staging_schema()` aligned on 26 columns.
- 80 local smoke-test rows produced no schema drift: no extra keys, no missing required fields.
- `hm_winner_labels`, `ai_winner_labels`, `hm_winner_support`, `ai_winner_support`, and `semantic_outlier` all serialized successfully.

## Read-only BigQuery checks
- `project_semantic_features`:
  - total rows: `582820`
  - rows with embeddings present: `582820`
  - rows with all HM fields NULL: `0`
  - rows with NaN/Inf in `embedding_vector`: `0`
- `project_semantic_features_rebuild_20260529_ai_knn_source`:
  - total rows: `518602`
  - rows with NaN/Inf in `embedding_vector`: `0`

## Practical pitfalls
- BigQuery Python client may fail with a 403 on job creation / project-use permission even when `bq query` works for read-only SELECTs.
- `embedding_vector` can come back from BigQuery as string arrays; convert elements to numeric locally before feeding sklearn.
- KNN may emit divide-by-zero / overflow / invalid-value warnings during matmul, but the pipeline can still complete and produce valid serialized rows; inspect the actual output and not the warning alone.

## Useful conclusion shape
- If target rows to update are already fully populated, Stage2 should be reported as format-valid but operationally a no-op on the current snapshot.
- If the user wants true end-to-end write confirmation, that requires a separate execution window where BigQuery MERGE/UPDATE is allowed.
