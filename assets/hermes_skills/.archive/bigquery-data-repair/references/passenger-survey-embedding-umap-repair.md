# Passenger survey embedding + UMAP rebuild repair

Use this reference when repairing `passenger_survey_pred_dashboard.project_semantic_features*` tables where historical/API-appended rows are missing `embedding_vector` and UMAP `x`/`y`.

## Durable pattern

1. Backup the target table first with `bq cp` using a timestamped suffix.
2. Normalize unavailable human labels explicitly:
   - For rows flagged `needs_hm_labels` that can no longer receive human labels, set all `hm_*_mark` fields to `NULL`.
   - Set `hm_consensus_score` and `hm_winner_support` to `NULL`.
   - Set `hm_winner_labels` to `[]`.
   - Replace the quality flag `needs_hm_labels` with a durable provenance flag such as `hm_labels_unavailable`.
3. Use a staging table for newly generated embeddings instead of updating the target row-by-row.
   - Key: `(appoint_no, opinion_no)`.
   - Fields: `embedding_vector ARRAY<FLOAT64>`, `embedded_at TIMESTAMP`.
   - Make the script resumable by anti-joining target missing rows against the staging table.
4. Vertex embedding can be called through the REST `predict` endpoint when the Python Vertex SDK/ADC quota-project path fails:
   - Endpoint shape: `https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:predict`
   - Header: `Authorization: Bearer $(gcloud auth print-access-token)`
   - Header: `x-goog-user-project: {project}`
   - Payload instances use `{"content": text, "task_type": "CLUSTERING"}`.
5. BigQuery Python client may inherit local ADC quota-project problems. If `gcloud bq` works but Python BigQuery calls fail with service-usage quota-project errors, create credentials from ADC and remove quota project:
   ```python
   from pathlib import Path
   from google.cloud import bigquery
   from google.oauth2.credentials import Credentials

   adc_path = Path.home() / ".config/gcloud/application_default_credentials.json"
   creds = Credentials.from_authorized_user_file(
       str(adc_path), scopes=["https://www.googleapis.com/auth/cloud-platform"]
   ).with_quota_project(None)
   client = bigquery.Client(project=project_id, credentials=creds)
   ```
6. After embeddings are staged, MERGE them back to the target only where `ARRAY_LENGTH(target.embedding_vector) = 0`.
7. For replacement UMAP coordinates that are known not comparable with old coordinates:
   - Select rows with `(x IS NULL OR y IS NULL)` and valid embeddings.
   - Sample 30,000 rows from that population to fit an initial UMAP model.
   - Save the fitted reducer locally with metadata (`joblib` is fine).
   - Transform all missing-UMAP rows with that same reducer.
   - Write `x`, `y`, `projected_at`, and model path to a staging table, then MERGE into the target.
8. Verify with counts:
   - total rows
   - distinct composite keys
   - duplicate/null keys
   - missing embedding rows
   - missing UMAP rows
   - `needs_hm_labels` vs `hm_labels_unavailable` flags
   - HM core fields null count

## Pitfalls

- BigQuery correlated subqueries in an UPDATE/MERGE `SET` expression can fail (`Correlated Subquery is unsupported in UPDATE clause`). If this happens, split flag cleanup into separate SQL or avoid mutating flags inside the same MERGE that updates embeddings/coordinates.
- Do not claim completion while a long Vertex embedding/UMAP job is still running. Report the background PID/session, log path, staging tables, and current counts, then set a monitor if needed.
- `gemini-embedding-001` returns 3072-dimensional vectors in this workflow; validate dimensions in the staging table before UMAP.
- The new UMAP x/y from a separately fitted model are not comparable to historical x/y. If the user explicitly accepts this tradeoff, record it in the report and model metadata rather than treating it as a bug.

## Example artifacts from the 2026-05-29 repair

- Target table: `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features_rebuild_20260529`
- Embedding staging table: `project_semantic_features_rebuild_20260529_embedding_stage`
- UMAP staging table: `project_semantic_features_rebuild_20260529_umap_xy_stage`
- Model path pattern: `data-rebuild-YYYYMMDD/run_embeddings_umap_YYYYMMDD/umap_reducer_YYYYMMDD.joblib`
