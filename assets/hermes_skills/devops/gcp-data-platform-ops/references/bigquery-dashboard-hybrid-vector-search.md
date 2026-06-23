# BigQuery dashboard hybrid keyword + vector search

Use this when adding semantic search to a BigQuery-backed dashboard whose source table already stores row embeddings.

## Discovery checklist

1. Confirm the exact dev/prod table from code constants/env, then inspect schema and table metadata before coding.
2. For large tables, use only schema, aggregate counts, and `LIMIT 2-3` samples. Avoid dumping rows.
3. Verify embedding readiness:
   - embedding column name and type, e.g. `embedding_vector FLOAT REPEATED`
   - row coverage: `COUNTIF(ARRAY_LENGTH(embedding_vector) > 0)`
   - dimension distribution: `ARRAY_LENGTH(embedding_vector)` grouped counts
   - norm sample: `SQRT(SUM(v*v))` over a bounded sample; if norm is ~1, dot product is usable as cosine similarity
   - presence/absence of `INFORMATION_SCHEMA.VECTOR_INDEXES`
4. Find the ETL embedding model and task type from the data-processing repo before generating query embeddings. Query embeddings must use the same model/task/region as stored row embeddings.

## Implementation pattern

- Preserve existing filters first: date window, labels, exact IDs.
- If no keyword/query is provided, keep the existing non-semantic ordering.
- If a keyword/query is provided:
  1. Generate a query embedding using the ETL model settings.
  2. Build a base CTE with existing filters and the existing opinion date expression.
  3. Compute `keyword_score`, `vector_score`, and optional `recency_score` in SQL.
  4. Compute `hybrid_score` from fixed weights, e.g. keyword 0.45, vector 0.50, recency 0.05.
  5. Bound candidates with a configurable `LIMIT` before pagination, especially when there is no vector index/partitioning.
  6. Return score fields in the API if useful for validation/debugging.
- If semantic search fails at runtime, consider falling back to the previous keyword LIKE path so the search UI remains usable.

## BigQuery SQL notes

- For normalized vectors, vector score can be dot product:
  `SUM(query_value * row_value)` joined by offsets from `UNNEST(query_vector)` and `UNNEST(embedding_vector)`.
- Clamp the score to `[0, 1]` if downstream ranking expects that range.
- Keep date fallback logic consistent between SELECT display and WHERE filtering; otherwise validation can look inconsistent.
- If current-month defaults return zero rows because the dataset lags behind today's month, verify with explicit date ranges from observed data before diagnosing search quality.

## Verification

- TDD: first add tests for scoring expression generation, dimension guards, and preservation of filters.
- Smoke test query embedding alone: length, first values, norm.
- Smoke test the API with a known date range and 2-3 result rows; report total, scores, and short text snippets.
- Do not treat a repo-wide pytest collection as authoritative if the workspace contains sibling repos; run the target repo's focused tests explicitly.
