# Passenger survey stage 1 tour_name recovery (2026-05)

Session notes:
- Stage 1 ai-label endpoint: `/report/customer-feedback/ai-label`
- Query window used: 2026-05-01 00:00:00 to 2026-05-31 23:59:59
- Payload fields observed included `appoint_no`, `opinion_no`, `tour_code`, `tour_name`, `tour_date`, `leader_Id_No`, `leader_name`, `tour_guide_seq_no`, `tour_guide_name`, `leader_and_guide_flag`, `create_time`, and AI label flags.

Key findings:
- `tour_name` exists in the API payload.
- `tour_name` is not present in the BigQuery `project_semantic_features` schema as inspected during the session.
- For survey ETL mapping/backfill, the stable join keys to use are `(appoint_no, opinion_no)`.
- `tour_code` should not be used as the primary mapping key when the user has explicitly requested `appoint_no / opinion_no` mapping.

Compact verification pattern:
1. Fetch one monthly window from `ai-label`.
2. Normalize with pandas and inspect columns.
3. Confirm `tour_name` presence and sample 2-3 rows.
4. Compare the target BigQuery row set by `(appoint_no, opinion_no)` before any backfill.

Caution:
- `tour_name` can be null on some returned rows; do not assume the field is always populated just because it exists.