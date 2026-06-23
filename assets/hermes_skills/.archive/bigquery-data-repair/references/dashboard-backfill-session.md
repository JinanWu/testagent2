# Dashboard backfill session notes

Concrete example from a dashboard repair workflow.

## Environment
- Project: `dev-cola-rd`
- Dataset: `passenger_survey_pred_dashboard`

## Tables involved
- Target report table: `opinion_tree_metrics_summary_snapshot`
- Backup created before repair: `opinion_tree_metrics_summary_snapshot_backup_20260519`
- Raw source table: `project_semantic_features`
- Helper/backfill table that did not line up with the target: `project_semantic_features__sentiment_backfill_20260514_042548`

## What happened
- The target report table was backed up first with `bq cp`.
- May raw rows were inspected from `project_semantic_features`.
- A helper backfill table existed, but join coverage against May rows was effectively zero.
- The correct fix was not to trust the helper table name; instead the monthly snapshot was recomputed from row-level data.

## Useful lessons
- Schema mismatch matters more than table naming.
- For a derived snapshot, inserting a corrected snapshot row can be safer than mutating the whole history.
- Always verify coverage with counts before writing.
- Keep the old snapshot intact as a rollback path.
