# Dashboard rowdata hierarchy check (2026-05-19)

Context: user asked whether the dashboard "rowdata" table could support deeper drill-down (product -> tour).

What was checked
- BigQuery project: `dev-cola-rd`
- Dataset: `passenger_survey_pred_dashboard`
- Table: `project_semantic_features`

Schema highlights
- `appoint_no` INTEGER REQUIRED
- `opinion_no` INTEGER REQUIRED
- `suggestion_describe` STRING
- `tour_code` STRING
- `tour_date` STRING
- `leader_name` STRING
- `tour_guide_name` STRING
- many sentiment/label columns

Coverage check
- total rows: 449,636
- rows with `tour_code`: 449,636
- rows with `tour_date`: 449,636
- rows with `leader_name`: 449,636

Takeaway
- The raw row-level source clearly contains tour-level identifiers and per-opinion data.
- It does not itself expose a ready-made product/tour hierarchy.
- If the UI needs product -> tour drill-down, confirm the product mapping source first (or derive it from `tour_code` via another table) before promising the extra level.

Useful SQL pattern
```sql
SELECT
  COUNT(*) AS total_rows,
  COUNTIF(tour_code IS NOT NULL) AS rows_with_tour_code,
  COUNTIF(tour_date IS NOT NULL) AS rows_with_tour_date,
  COUNTIF(leader_name IS NOT NULL) AS rows_with_leader_name
FROM `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`;
```