# BigQuery dashboard hybrid opinion search

Use this when a dashboard opinion-search API must combine keyword matching with precomputed BigQuery embeddings.

Session pattern:
- First inspect the dev BigQuery table with schema reads, aggregates, and `LIMIT` samples only. Large dashboard tables may be >10 GB and unpartitioned; avoid unconstrained row dumps.
- Check whether the opinion table already has an embedding column before planning a backfill. In this case `project_semantic_features.embedding_vector` was `FLOAT REPEATED`, complete for all rows, 3072-dimensional, and unit-normalized, so no embedding backfill was needed.
- Find the ETL/job repo that generated embeddings and reuse its exact model settings for query embeddings. The passenger-survey dashboard job used Vertex AI `gemini-embedding-001`, location `us-central1`, `TextEmbeddingInput(..., task_type="CLUSTERING")`, batch size 100. Query-time embeddings must use the same model/task type to stay in the same vector space.
- If stored vectors are unit-normalized, BigQuery vector score can be a dot product over aligned offsets:
  `SELECT SUM(query_value * opinion_value) FROM UNNEST(query_vector) WITH OFFSET JOIN UNNEST(embedding_vector) WITH OFFSET USING(offset)`.
- Preserve existing non-text filters first: date window, labels, appoint/order number. If the API's default date window has no current data, pass a known populated window during validation to avoid false “no results” conclusions.
- Use a hybrid score rather than replacing keyword search outright. A practical first version:
  `hybrid_score = 0.45 * keyword_score + 0.50 * vector_score + 0.05 * recency_score`.
- Expose score fields (`keyword_score`, `vector_score`, `recency_score`, `hybrid_score`) in API results during early rollout so ranking quality can be inspected.
- If there is no BigQuery vector index and the table is unpartitioned/large, keep candidate limits bounded and report that this is a first version. Consider VECTOR_INDEX/VECTOR_SEARCH or partitioning later if latency/cost is unacceptable.
- Keep a safe fallback: if query embedding generation or hybrid SQL fails, fall back to the previous LIKE keyword search instead of breaking the endpoint.

Minimal validation recipe:
1. Add unit tests for keyword score SQL, 3072-dimension vector literal validation, and preservation of date/label filters in hybrid SQL.
2. Smoke-test query embedding locally and verify length/norm.
3. Call the actual API/TestClient with a populated date window and `limit=2-3`; confirm status 200 and inspect score fields plus returned text.
4. When running pytest, target the repo/test file explicitly if the parent workspace contains multiple unrelated repos; otherwise pytest may collect tests from sibling directories and fail for unrelated dependencies.
