---
name: bigquery-data-repair
description: BigQuery dataset inspection, backup, reconciliation, and safe backfill of warehouse/report tables.
---

# BigQuery data repair and backfill

Use this skill when the task is to inspect a BigQuery dataset, back up a table, reconcile a target window, and backfill or repair values safely.

## Core workflow

1. Confirm the exact project, dataset, and table names before touching anything.
2. Inspect schema first. Do not assume the backfill source has the same columns as the target.
3. If the task is cost attribution or billing-export analysis, verify the billing account, export dataset, dataset location, and query-job project before writing or querying.
4. Before any BigQuery CLI read in a new shell session, preflight auth:
   - check the active account with `gcloud auth list`
   - verify the token can still refresh with `gcloud auth print-access-token`
   - if refresh fails in a non-interactive shell, re-authenticate in an interactive flow and then re-run the token check before retrying `bq`
   - if multiple accounts exist, explicitly set the intended account before the query
5. Avoid piping `bq` output directly into an interpreter when a safer two-step inspection will do; capture JSON to a file or inspect the raw output first, then parse it.

Support files:
- `references/dashboard-backfill-session.md` contains a concrete monthly dashboard repair example from this workflow.
- `references/dashboard-rowdata-hierarchy.md` captures a rowdata/hierarchy check where the raw table had tour-level identifiers but no ready-made product/tour hierarchy.
- `references/passenger-survey-tour-name-stage1.md` records the survey ETL case where stage 1 `ai-label` does include `tour_name`, and mapping/backfill must join on `(appoint_no, opinion_no)`.
- `references/passenger-survey-tour-name-stage3.md` records the survey ETL stage 3 case and its strict mapping rules.
- `references/passenger-survey-may-2026-stage3-filtered-tree.md` records the May 2026 follow-up where rows with empty `tour_name` were filtered out and the remaining rows were turned into a stage 3 tree.
- `references/passenger-survey-sentiment-empty-upstream.md` records the May 2026 survey finding that `ai_sentiment_label` / `ai_sentiment_score` were empty at the API source, not lost in BigQuery or serialization.
- `references/passenger-survey-sentiment-backfill-window-mismatch.md` records a later check where a candidate sentiment backfill table had rich sentiment values but a completely different `(appoint_no, opinion_no)` window, so it could not explain the target month.
- `references/passenger-survey-rebuild-source-triage.md` records the broader passenger-survey rebuild pattern: classify full row-level base tables vs helper/backfill tables, avoid embedding scans, use composite-key coverage, and handle encrypted historical 7z archive inspection safely.
- `references/passenger-survey-full-rebuild-workflow.md` records the end-to-end passenger-survey rebuild workflow: stage1/stage2/stage3 semantics, historical archive staging, provenance/quality flags, old/new API supplementation, and verification counts.
- `references/passenger-survey-vertex-embedding-umap-backfill.md` records the safe pattern for marking unrecoverable HM labels as NULL, using Vertex REST embedding backfills with staging/merge, accelerating with parallel batches, fitting a new non-comparable UMAP model, and reporting long-running progress.
- `references/passenger-survey-hm-consensus-vector-search.md` records the follow-up pattern for recomputing `hm_consensus_score`, `hm_winner_labels`, and `hm_winner_support` with a compact BigQuery `VECTOR_SEARCH` KNN source while leaving unrecoverable HM rows NULL/empty.
- `references/passenger-survey-hm-consensus-local-fallback.md` records the local `pynndescent` KNN fallback for HM consensus when BigQuery `VECTOR_SEARCH` or its CLI wrapper is too slow/fragile for a large one-off backfill.
- `references/cloud-run-gemini-cost-attribution.md` captures the Cloud Run + Gemini cost-attribution pattern using request logs plus billing CSVs when raw billing export access is limited.
- `references/bq-auth-preflight.md` records the CLI auth preflight and retry pattern for BigQuery sessions.

When a user says "rowdata", verify the actual table schema and key coverage before assuming the source can support deeper drill-down levels.
3. Measure the target window:
   - total rows
   - null/empty counts for the fields to repair
   - date distribution
   - key coverage
4. Identify the true repair source:
   - raw row data
   - an exported backfill table
   - upstream pipeline output
   - a recomputed derived table
   - for Gemini/Vertex AI cost questions, a monthly billing CSV plus Cloud Run request logs can still support attribution when raw export access is unavailable
5. Create a backup before any write operation.
   - Prefer `bq cp` for whole-table backups when the target is a table.
   - Name backups with an explicit timestamp suffix.
6. Test join coverage between source and target before writing.
   - Check how many rows match.
   - Check whether the source carries the same primary key or business key.
   - Sample mismatches in both directions.
7. Only after coverage is understood, apply the repair.
   - For derived snapshot tables, prefer inserting a corrected snapshot or rebuilding the affected window.
   - For raw tables, prefer targeted updates only when the source key and value semantics are fully verified.
8. Verify after the write.
   - rerun counts
   - spot-check a few repaired rows
   - confirm the new snapshot or updated table is the one downstream reads

## Key pitfalls

- Do not trust a helper/backfill table just because its name looks related to the target.
- Do not join on a guessed key. Confirm the actual business key with samples and coverage checks first.
- Do not write back before creating a backup of the table you may need to restore.
- Do not try to repair a derived report table by mutating unrelated upstream raw data unless that is the intended source of truth.
- If a window has partially mapped rows, inspect the unmapped set instead of assuming the source is empty.

## Practical checks

- If the user says “rowdata”, treat it as the raw row-level source and verify what that means in the current repo or BigQuery schema.
- If the repair source lacks the date column, use the target window filter from the target table and join on business keys.
- If the repair is for a monthly dashboard snapshot, consider inserting a corrected monthly snapshot row rather than overwriting historical rows.
- For nested dashboard snapshot JSON, distinguish business nodes from metadata fields before blaming the data. A frontend that renders every array item as a child can surface fake nodes such as `kind`, `opinion_count`, or `head_weighted_mean`; verify the intended hierarchy level first.
- For survey ETL backfills, prefer the user-specified business key. In this project, use `(appoint_no, opinion_no)` for joining stage 1 payloads to the warehouse, and do not substitute `tour_code` unless the user explicitly asks for that mapping.
- When a target table lacks a requested display field such as `tour_name`, verify both the upstream API payload and the BigQuery schema before planning a backfill. If the API payload contains `tour_name`, use that field directly and preserve nullability checks row-by-row.
- If the user explicitly wants to discard incomplete rows and continue, filter them out first, then build the stage 3 tree from the remaining complete rows. Report the dropped counts separately (`dropped_missing_tour_name_rows`, `unmapped_prefix_rows`) instead of hiding them inside the tree result.
- For survey sentiment fields, treat a blank/`0.0` pattern as a data-quality or upstream-availability question first. In the observed 2026-05 ai-label payload, the serializer was pass-through and the API source itself had no non-empty `ai_sentiment_label` values.
- If a BigQuery helper/source table exists for sentiment backfill, do not assume it feeds the target window. Measure key overlap on `(appoint_no, opinion_no)` and compare key ranges before calling it the upstream source. In one case, the backfill table had 409,536 rows with valid Positive/Negative sentiment, but zero key overlap with the May 2026 target window.
- For passenger-survey rebuilds, separate table role classification from raw row count: a helper/backfill table can be large and fully populated but still unsuitable as the row-level base if it lacks text, tour, label, embedding/UMAP, and consensus columns. Use the full row-level table as base and helper tables only to enrich overlapping keys.
- For passenger-survey stage semantics: stage 1 (`ai-label`) creates/merges row-level rows with AI labels, embeddings, and UMAP while HM stays NULL; stage 2 (`label-analyze`) only updates existing BigQuery keys with HM plus KNN/consensus and skips missing keys; stage 3 reads the row-level table to build organization-tree metrics/summaries and does not call label APIs.
- For full passenger-survey rebuilds, create a new rebuild table with provenance fields (`rebuild_source`, `rebuild_loaded_at`, `rebuild_quality_flags`) rather than overwriting the original table. Preserve non-inserted missing rows in an audit table, and flag appended rows that still need embeddings, UMAP, AI/HM labels, sentiment, or consensus recomputation.
- When HM labels are no longer recoverable, mark all HM label/consensus fields as explicit NULL/empty winner arrays and replace `needs_hm_labels` with a provenance flag such as `hm_labels_unavailable`; do not leave the rows looking like a pending recoverable backlog.
- For large passenger-survey embedding/UMAP backfills, use staging tables plus idempotent `MERGE` back to the target. If the user accepts non-comparable UMAP coordinates, fit a fresh UMAP model on the newly embedded/missing-UMAP population (for example a 30k sample), save the model with metadata, transform the remaining rows, and report that old/new x/y spaces are not comparable.
- For Vertex embedding throughput, probe batch-size limits and parallelize requests only after verifying the endpoint accepts the payload size. A proven pattern for `gemini-embedding-001` was REST calls with `task_type=CLUSTERING`, batch size 250, six workers, staging flushes around 2k rows, and target merge after all staging writes.
- After passenger-survey embeddings/UMAP are complete, recompute HM consensus fields only for HM-available rows. Use a compact BigQuery VECTOR_SEARCH source table containing keys, embeddings, HM marks, and `hm_true_count`; create an IVF vector index; search `top_k=21`, drop the self-neighbor, keep k=20, aggregate HM label support, stage results, and MERGE only `hm_consensus_score`, `hm_winner_labels`, and `hm_winner_support`. Rows flagged as unrecoverable HM should remain NULL/empty rather than being filled with synthetic consensus. If the BigQuery query wrapper is killed or the stage table stays empty, verify actual target/stage counts and switch to the local `pynndescent` fallback in `references/passenger-survey-hm-consensus-local-fallback.md` instead of repeatedly rerunning the same long query.
- If local Vertex SDK or BigQuery clients fail because ADC quota-project permissions are missing, prefer a token/header or quota-project workaround documented in `references/passenger-survey-vertex-embedding-umap-backfill.md`; do not hard-code a lasting conclusion that the service/tool is unavailable.
- For current-year passenger-survey repair, stage new and old API extracts separately, combine to one row per `(appoint_no, opinion_no)` using non-empty `COALESCE`, update existing display fields such as `tour_name` first, then insert still-missing API keys with quality flags. Verify staged API coverage returns zero missing keys afterward.
- For passenger-survey embedding/UMAP repairs, use resumable staging tables keyed by `(appoint_no, opinion_no)`: stage Vertex-generated `embedding_vector` before merging, then stage UMAP `x`/`y` before merging. If a new UMAP model is fit only on the repaired population, save the reducer with metadata and state clearly that its coordinates are not comparable with the old model.
- For current-year passenger-survey repair, stage new and old API extracts separately, combine to one row per `(appoint_no, opinion_no)` using non-empty `COALESCE`, update existing display fields such as `tour_name` first, then insert still-missing API keys with quality flags. Verify staged API coverage returns zero missing keys afterward.
- Avoid `SELECT *` and avoid downloading `embedding_vector` when comparing candidate survey tables. Use scalar completeness queries and schema inspection first. For composite key counts in BigQuery, prefer `COUNT(DISTINCT CONCAT(CAST(appoint_no AS STRING), '#', CAST(opinion_no AS STRING)))` rather than `COUNT(DISTINCT STRUCT(...))`, which can fail.
- If Vertex AI Python SDK or Python BigQuery clients fail because local ADC has a quota-project/service-usage permission problem while CLI auth works, do not conclude the API is unavailable. For Vertex embeddings, call the REST `predict` endpoint with a `gcloud auth print-access-token` bearer token and `x-goog-user-project`. For Python BigQuery, build credentials from ADC and remove the quota project with `.with_quota_project(None)`.
- BigQuery can reject correlated subqueries inside DML `UPDATE SET` expressions. When cleaning repeated quality flags during a MERGE, either pre-clean flags in a separate statement or avoid correlated `UNNEST` logic in the same MERGE that writes large generated fields.
- When historical survey sources are encrypted 7z archives, inspect safely before extraction. `bsdtar` cannot read encrypted 7z headers; `py7zz` provides a bundled official `7zz` binary that can validate/list archives. If official `7zz` reports a header/password error, stop and ask for a corrected password rather than guessing or partially extracting.
- When sharing sample snapshot data with a user, keep the output compact: show 2-3 representative rows or paths and summarize the tree shape instead of dumping the whole JSON.
- For billing-export investigations, confirm whether the billing dataset is in the same project as the target app; do not assume the active gcloud project is the billing-export source.

## Verification checklist

- Backup table exists.
- Source and target schemas are understood.
- Join coverage is measured.
- Repair scope is limited to the intended window.
- Post-write validation matches the expected row counts and values.

See `references/dashboard-backfill-session.md` for a concrete dashboard repair example and gotchas.
See `references/billing-export-diagnostics.md` for billing-export / Gemini cost attribution workflow and pitfalls.
See `references/opinion-tree-json-debugging.md` for a concrete case where a dashboard snapshot’s metadata fields were mistaken for tree children.