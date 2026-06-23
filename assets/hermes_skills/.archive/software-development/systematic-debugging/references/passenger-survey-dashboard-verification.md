# Passenger Survey Dashboard Verification Notes

Session-derived checklist for the passenger-survey dashboard rerun.

## What to verify after a rerun

1. Row data landed in the target table
   - Project: `dev-cola-rd`
   - Dataset: `passenger_survey_pred_dashboard`
   - Table: `project_semantic_features`
   - Check counts by `tour_date` month (e.g. `202604`, `202605`) and the non-null HM fields.

2. Snapshot table metadata is consistent
   - Table: `opinion_tree_metrics_summary_snapshot`
   - Required columns:
     - `run_id`, `run_ts`
     - `source_project`, `source_dataset`, `source_table`
     - `tour_date_start`, `tour_date_end`
     - `summary_model`
     - `metrics_tree`, `summary_tree`
   - Confirm the latest row points back to the intended source project/dataset/table.

3. Tree drill-down is present
   - Read the `summary_tree` JSON.
   - Confirm root children exist and can be traversed to leaf nodes.
   - A leaf node should look like:
     - `kind: leaf`
     - `path: [...]`
     - `summary: ...` or `null` when the leaf has no opinions.

## Useful BigQuery probes

- Latest snapshot metadata:
  ```sql
  SELECT source_project, source_dataset, source_table, run_ts,
         tour_date_start, tour_date_end, summary_model
  FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
  ORDER BY tour_date_start DESC
  LIMIT 1
  ```

- Recent monthly row counts:
  ```sql
  SELECT SUBSTR(tour_date,1,6) yyyymm,
         COUNT(*) c,
         COUNTIF(hm_praise_mark IS NOT NULL OR hm_complain_mark IS NOT NULL OR hm_suggestion_mark IS NOT NULL) hm_nonnull
  FROM `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`
  WHERE tour_date BETWEEN "20260401" AND "20260531"
  GROUP BY yyyymm
  ORDER BY yyyymm
  ```

- Drill-down inspection:
  ```sql
  SELECT JSON_QUERY(summary_tree, "$.children") AS root_children,
         JSON_QUERY(summary_tree, "$.children.\"中國\"") AS china_node,
         JSON_QUERY(summary_tree, "$.children.\"中國\".children.\"江南\"") AS jiangnan_leaf
  FROM `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
  ORDER BY tour_date_start DESC
  LIMIT 1
  ```

## Common pitfalls

- A snapshot can exist even when `opinion_count = 0`; don't treat existence as proof of useful drill-down data.
- Latest-by-date may still be a simulated/mock snapshot unless `source_project/source_dataset/source_table` are checked.
- `summary_tree` can contain the hierarchy even when a leaf has `summary: null`.
- For this project, `tour_date` is the key field for monthly grouping; rows without `tour_date` should not be treated as valid month data.
