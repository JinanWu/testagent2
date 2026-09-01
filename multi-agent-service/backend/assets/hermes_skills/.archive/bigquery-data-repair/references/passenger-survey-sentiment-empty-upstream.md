# Passenger survey sentiment fields: upstream-empty finding

Context:
- Project: `passenger-survey-dashboard-jobs` / `passenger-survey-dashboard`
- Stage 1 source: ai-label API payload for 2026-05
- Join key used for reconciliation: `(appoint_no, opinion_no)`

Observed behavior:
- The API payload includes `ai_sentiment_label` and `ai_sentiment_score` fields.
- In the 2026-05 payload analyzed during the session, `ai_sentiment_label` had no non-empty values at all.
- `ai_sentiment_score` was either `0.0` or null, mirroring the empty-label rows.
- The local serializer does not compute sentiment; it passes through the API value when present and otherwise emits null/blank-compatible output.

Implication:
- If the downstream table or tree shows blank/0 sentiment, the first assumption should be that the upstream API did not populate sentiment for that window, not that BigQuery or serialization stripped it.
- Verify with a raw API sample before changing warehouse code.

Follow-up from the May 2026 source-table check:
- A separate BigQuery table named `project_semantic_features__sentiment_backfill_20260514_042548` did contain valid sentiment values (`Positive` / `Negative`) for 409,536 rows.
- However, that table did not join to the May 2026 target window on `(appoint_no, opinion_no)` at all; the key ranges were different and the join matched 0 rows.
- So an existing helper/backfill table is not automatically the upstream source for the blank May sentiment values.

Practical check:
- Compare the raw API JSON to the warehouse rows using `(appoint_no, opinion_no)`.
- Count non-empty labels and non-zero scores separately.
- If all labels are empty, treat sentiment as unavailable data rather than a downstream transform bug.
