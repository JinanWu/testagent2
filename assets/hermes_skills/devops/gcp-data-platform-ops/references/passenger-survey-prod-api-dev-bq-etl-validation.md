# Passenger survey ETL: prod API → dev BigQuery validation

Use this reference when validating the passenger-survey / mood-index ETL locally while reading production survey APIs and writing only to the dev BigQuery dataset.

## Known-good environment split

- Prod API mode: pass `--api-env production`.
- Dev BigQuery target observed in prior validation:
  - project: `dev-cola-rd`
  - dataset: `passenger_survey_pred_dashboard`
  - table: `project_semantic_features`
  - Stage3 report table: `opinion_tree_metrics_summary_snapshot`
- Vertex AI embedding may need to run from the lab project rather than the BigQuery project:
  - GCP project id is `lab-cola-rd` (not `cola-rd-lab`).
  - Set both `GOOGLE_CLOUD_QUOTA_PROJECT=lab-cola-rd` and `EMBEDDING_PROJECT_ID=lab-cola-rd`.
  - Smoke test first with `python -m embedding_pipeline.embedding_smoke --project-id lab-cola-rd --location us-central1 --model gemini-embedding-001`.

## Safe execution sequence

1. Confirm repo, branch, and target table names.
2. Run unit/doctest/compile checks.
3. Probe prod APIs by date range before writing; keep counts by endpoint.
4. Back up the dev BigQuery target and Stage3 report table with timestamped `bq cp` before any write.
5. Run Stage1/Stage2 against a bounded date window; redirect logs to `output/` and use a tracked background process.
6. Verify with aggregate BigQuery queries after writes:
   - rows touched by `ingested_at` window,
   - `ARRAY_LENGTH(embedding_vector)>0`,
   - HM null vs filled counts,
   - consensus/outlier populated counts,
   - tour_date/effective date ranges.
7. Do not let Stage3 append a snapshot until both structural readiness and scoring readiness are true.

## Important pitfalls observed

- Stage2 API date semantics can differ from Stage1. In one June run, Stage2 returned 8504 rows, BigQuery matched 7232, 1272 were missing, and 1315 already had HM. Treat missing Stage2 keys as a coverage question, not an automatic failure.
- Stage3 scoring requires non-empty sentiment labels (`Positive` / `Negative`) and usable `ai_sentiment_score`. If `ai_sentiment_label` is blank and score is 0, Stage3 may load rows and build structure but produce `scored_count=0` and root metrics `None`. Stop before appending the snapshot and ask whether to fix API/mapping, accept summary-only, or change scoring logic.
- Stage3 taxonomy mapping should report unmapped prefixes. Small unmapped counts may be acceptable, but list representative `tour_code`/`tour_name` before deciding.
- `both` includes Stage3 in this repo. If the user only asked for ingestion/HM validation, prefer `--stage stage1` then `--stage stage2`, or be ready to stop before Stage3 snapshot write if metrics are not valid.

## Example command shape

```bash
export GOOGLE_CLOUD_QUOTA_PROJECT=lab-cola-rd
export EMBEDDING_PROJECT_ID=lab-cola-rd
export MODEL_NAME=gemini-embedding-001
export BIGQUERY_STAGE3_REPORT_TABLE=opinion_tree_metrics_summary_snapshot

python -m embedding_pipeline.cli \
  --stage both \
  --api-env production \
  --project-id dev-cola-rd \
  --dataset-id passenger_survey_pred_dashboard \
  --table-id project_semantic_features \
  --location us-central1 \
  --start-date '2026-06-01T00:00:00' \
  --end-date '2026-06-15T23:59:59' \
  --stage2-start-date '2026-06-01T00:00:00' \
  --stage2-end-date '2026-06-15T23:59:59'
```

If Stage3 is not intended or scoring readiness is uncertain, use separate Stage1 and Stage2 commands instead of `both`.
