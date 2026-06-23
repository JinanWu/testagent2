# Passenger survey May 2026: filtered stage3 tree note

Session-specific note for the passenger-survey ETL/dashboard repair.

## What happened
- The May 2026 BigQuery monthly window had 6,578 rows in `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`.
- After joining on `(appoint_no, opinion_no)`, 87 matched rows still had empty `tour_name` in the stage 1 API payload.
- Stage 3 classification also produced 30 rows that could not be placed in the existing prefix-to-tree mapping (`tour_code` prefix missing from the organizational tree).

## What to do when the user asks to "throw away those rows and make the tree"
- Filter out the rows with empty `tour_name` before building the tree.
- Build the stage 3 tree from the remaining matched rows only.
- Keep the dropped counts separate:
  - `dropped_missing_tour_name_rows`
  - `unmapped_prefix_rows`
- Report the kept row count and the root tree count so the user can verify the scope.

## Important nuance
- Stage 3 structure is driven by `tour_code` prefix, not `tour_name`.
- Dropping rows with missing `tour_name` is safe only when the request is to build a tree from the remaining complete rows; it is not a repair/backfill.

## Practical verification
- Confirm the key join uses `(appoint_no, opinion_no)`.
- Check counts before and after filtering.
- If `unmapped_prefix_rows` is non-zero, report them separately instead of silently folding them into the tree.
- If the user asks for a deliverable file, write the filtered tree snapshot to a path in the workspace and include the path in the reply.
