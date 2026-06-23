# Passenger-survey / mood-index dev sentiment backfill + Stage3 validation

Use this recipe when the dashboard ETL is being validated in dev against prod-like/prod source data and Stage3 metrics are blocked because sentiment fields are blank.

## When it applies

- Source rows already exist in the dev BigQuery dashboard table.
- Stage1/Stage2 data shape is present, including embeddings/consensus where expected.
- Stage3 `metrics_tree` or snapshot scoring shows `scored_count=0`, `None`, or missing weighted means because `ai_sentiment_label` / `ai_sentiment_score` are blank.
- The user wants a dev validation path without changing prod outputs.

## Safe backfill pattern

1. Verify the environment split before writing:
   - prod/prod-like source API flag if relevant
   - dev BigQuery project/dataset/table target
   - Vertex/quota project if model calls need a third project
2. Measure the target window before the write:
   - target row count
   - `COUNTIF(ai_sentiment_label IS NOT NULL AND ai_sentiment_label != '')`
   - embedding/consensus completeness if Stage3 also depends on them
3. Prefer direct model invocation for one-field historical repairs when the service wrapper has unnecessary side effects.
   - For passenger-survey-pred, direct sentiment scoring through the in-repo sentiment analyzer can be safer than calling the production prediction API if the API also embeds/publishes downstream messages.
4. Dry-run a tiny sample first, writing no BigQuery rows.
   - Capture 2-5 examples with input id/text, predicted label, and score.
5. Write to a staging table or temp table with only the intended keys plus:
   - `ai_sentiment_label`
   - `ai_sentiment_score`
6. MERGE back to the dev target table, updating only those two sentiment fields unless the user explicitly approves more columns.
7. Verify with aggregate counts, not a table dump:
   - target rows in date window
   - nonblank sentiment count
   - positive-score count
   - label distribution
   - min/avg/max score by label
   - downstream Stage3 `opinion_count`, `scored_count`, weighted means
8. Drop/verify cleanup of any staging tables after the final MERGE.

## Stage3 validation when Gemini summary hangs

If Stage3 reaches metrics calculation but hangs during Gemini summary generation:

- Treat this as a separate summary-generation problem, not a failed sentiment/metrics backfill.
- Do not let an unbounded summary call block proof that metrics are healthy.
- Run or add a metrics-only snapshot mode that:
  - writes the real `metrics_tree`
  - writes an explicitly labeled stub/empty `summary_tree`
  - sets `summary_model` to a clear non-Gemini marker such as `metrics-only-validation-stub`
  - sets `summary_kind` or equivalent metadata so the row cannot be mistaken for a real semantic summary
- Verify the latest snapshot fields with BigQuery `JSON_VALUE`/aggregate queries.

## Durable code improvements to recommend

When the formal Stage3 still needs the full semantic summary pass, add:

- per-Gemini-call timeout
- per-node progress logs before and after each summary call
- checkpoint/resume for hierarchical summaries
- fail-open summary behavior: summary failure should return an empty/flagged summary and still allow metrics persistence when the user is validating metrics
- CLI switches such as `--skip-summary`, `--summary-model`, and `--summary-timeout-seconds`

## Reporting shape for the user

Keep the report operational and quantified:

- target env/project/dataset/table
- date window
- exact fields updated
- dry-run sample count and result distribution
- write count / affected rows
- post-write aggregate verification
- Stage3 `opinion_count`, `scored_count`, weighted means
- whether staging tables remain
- any blocker separated by layer, e.g. “sentiment + metrics fixed; Gemini summary still hangs”

Avoid claiming “full Stage3 passed” when only the metrics-only snapshot passed. Say exactly which layer was validated.