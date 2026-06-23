# Passenger survey sentiment backfill window mismatch

Context:
- Project: `dev-cola-rd.passenger_survey_pred_dashboard`
- Target table for the 2026-05 analysis: `project_semantic_features`
- Candidate helper table examined later: `project_semantic_features__sentiment_backfill_20260514_042548`
- Join key used for reconciliation: `(appoint_no, opinion_no)`

What was checked:
- The helper table had 409,536 rows and schema:
  - `appoint_no` INTEGER REQUIRED
  - `opinion_no` INTEGER REQUIRED
  - `ai_sentiment_label` STRING NULLABLE
  - `ai_sentiment_score` FLOAT NULLABLE
- The helper table’s sentiment values were rich:
  - labels: `Positive` and `Negative`
  - scores ranged from about 0.5000 to 0.9999
- But the key ranges did not overlap the May 2026 target rows:
  - helper table appoint_no range: 280,649–341,331
  - target May 2026 appoint_no range: 346,535–365,895
  - helper table opinion_no range: 813,412–231,235,945
  - target May 2026 opinion_no range: 203,153,213–218,143,806
- A left join on `(appoint_no, opinion_no)` from the May 2026 target table to this helper table returned 0 joined rows.

Lesson:
- Do not assume a “sentiment backfill” table is the upstream source for the current month/window just because the name sounds related.
- Always compare key coverage and min/max key ranges before using a helper table as an upstream source.
- If overlap is 0, the table is irrelevant to the target repair, even when it contains valid sentiment values.
