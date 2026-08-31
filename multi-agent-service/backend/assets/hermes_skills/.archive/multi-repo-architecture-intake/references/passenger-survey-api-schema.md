# Passenger Survey API Schema Recon Notes

This reference captures the minimal-token pattern for inspecting the passenger-survey model-labeling APIs.

## Endpoints

- `ai-label`
  - Production: `https://feedback-survey-service-586561863834.asia-east1.run.app/report/customer-feedback/ai-label`
  - Non-production: `https://sit-survey-api.colatour.org/report/customer-feedback/ai-label`
  - Purpose: Stage 1 / AI-only labels plus passenger-survey extra fields.

- `label-analyze`
  - Production: `https://feedback-survey-service-586561863834.asia-east1.run.app/report/customer-feedback/label-analyze`
  - Non-production: `https://sit-survey-api.colatour.org/report/customer-feedback/label-analyze`
  - Purpose: Stage 2 / HM + AI labels.

## Stable schema notes

From repo code and docs:
- `ai-label` returns 29 fields in the current schema.
  - Base fields include `appoint_no`, `opinion_no`, `suggestion_describe`, `tour_code`, `tour_date`, `leader_Id_No`, `leader_name`, `tour_guide_seq_no`, `tour_guide_name`, `leader_and_guide_flag`, `create_time`.
  - AI fields use the `ai_*_mark` prefix.
  - Extra fields: `ai_sentiment_label`, `ai_sentiment_score`.
- `label-analyze` returns 36 fields in the current schema.
  - Base fields: `appoint_no`, `opinion_no`, `suggestion_describe`, `create_time`.
  - HM fields use the `hm_*_mark` prefix.
  - AI fields use the `ai_*_mark` prefix.
  - No tour/sentiment extra fields are expected from this API.

## Minimal-sample inspection pattern

When the user wants field examples, do not dump a large date range.

Preferred approach:
1. Read repo docs/tests first to confirm the stable field set.
2. If live API access is needed, query a narrow time window first.
3. Inspect only the first 2-3 rows.
4. Report:
   - field count
   - field names grouped by category
   - 1-2 representative sample values per category
   - any nullability quirks (for example `create_time` or sentiment fields may be null in some ranges)

## Failure handling

- If a broad date-range request times out, do not keep widening retries blindly.
- Fall back to code/docs/schema files and only retry a narrower window if live confirmation is still necessary.
- Prefer concise reporting over exhaustive dumps; the goal is schema confirmation, not full data export.
