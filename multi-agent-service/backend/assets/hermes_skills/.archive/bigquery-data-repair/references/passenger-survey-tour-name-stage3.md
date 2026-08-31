# Passenger survey tour-name / stage3 repair note

Context: This note came from a May 2026 repair session for the `passenger-survey` ETL/dashboard stack.

## What was verified
- Stage 1 API (`ai-label`) returns `tour_code`, `tour_date`, leader / guide fields, and sentiment fields, but no `tour_name`.
- The ETL code path that writes stage 1 data only serializes `AI_LABEL_EXTRA_FIELDS`, which currently includes `tour_code` but not `tour_name`.
- BigQuery target table `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features` has no `tour_name` column.
- A May 2026 sample query showed 6,578 rows, 617 distinct `tour_code` values, and 0 null `tour_code` values.

## Stage 3 behavior
- Stage 3 groups rows by `tour_code` prefix and places them into a fixed organizational tree of routes / regions.
- The current stage 3 tree uses route / region labels, not a `tour_name` field from the API.
- `tour_name` should not be invented from the stage 3 tree unless there is a separate authoritative mapping source.

## Safety rule
- Do not backfill or upload a new `tour_name` field unless every target row is mapped by an authoritative source.
- If any row is unmapped or ambiguous, stop and report instead of writing to BigQuery.

## Useful verification commands
- `bq show --schema --format=prettyjson <project>:<dataset>.<table>`
- `bq query --nouse_legacy_sql --format=prettyjson 'SELECT COUNT(*), COUNT(DISTINCT tour_code) ...'`
- Sample the monthly window with `LIMIT 2-3` rows first before any bulk repair.
