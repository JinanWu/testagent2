# Passenger survey HM consensus backfill with BigQuery VECTOR_SEARCH

Use this reference when passenger-survey rows already have `embedding_vector` and UMAP `x`/`y`, but HM-derived fields need to be recomputed or backfilled:

- `hm_consensus_score`
- `hm_winner_labels`
- `hm_winner_support`

## Context

Some passenger-survey rows may have unrecoverable human labels. Those rows should remain explicit NULL/empty HM-derived values and should not be treated as a pending recoverable backlog. Only rows whose `hm_*_mark` fields are available should participate in HM consensus computation.

## Durable pattern

1. Verify the previous embedding/UMAP repair is complete before doing HM consensus:
   - `COUNTIF(ARRAY_LENGTH(embedding_vector)=0) = 0`
   - `COUNTIF(x IS NULL OR y IS NULL) = 0`
2. Build a compact KNN source table that includes only:
   - `(appoint_no, opinion_no)`
   - `embedding_vector`
   - all `hm_*_mark` fields
   - `hm_true_count`, the number of true HM labels for the row
   - filter to `ARRAY_LENGTH(embedding_vector) > 0 AND hm_compare_mark IS NOT NULL`
3. Create a BigQuery vector index on the compact source table:
   - `CREATE VECTOR INDEX ... ON source_table(embedding_vector) OPTIONS(index_type='IVF')`
   - Check `INFORMATION_SCHEMA.VECTOR_INDEXES`; `index_status='ACTIVE'` is enough to run, but coverage may still be warming.
4. Use `VECTOR_SEARCH` against the compact source table, not the full production/rebuild table. Avoid `SELECT *` from the original wide table because embedding arrays make output and scan cost huge.
5. Search with `top_k => 21`, exclude the query row itself, then keep the nearest 20 neighbors:
   - self match usually appears with distance 0
   - use `QUALIFY ROW_NUMBER() OVER (PARTITION BY query key ORDER BY distance) <= 20`
6. Aggregate neighbor label support with `COUNTIF(b_hm_x_mark)` for every HM field.
7. Compute consensus:
   - if the query row has one or more true HM labels, only compare support for labels that are true on the query row
   - `hm_consensus_score = max_support / k`
   - `hm_winner_support = max_support / k`
   - `hm_winner_labels` are the true query labels tied at `max_support`
   - if the query row has no true HM labels, treat `['無標籤']` as the winner and score/support as `support_no_label / k`
8. Write results to a staging table first, then `MERGE` only the three HM-derived fields back to the rebuild/target table.
9. Post-verify:
   - HM-available rows should have non-null `hm_consensus_score` and `hm_winner_support`
   - rows flagged `hm_labels_unavailable` should remain NULL/empty for HM-derived fields

## SQL shape

```sql
CREATE OR REPLACE TABLE dataset.hm_knn_source AS
SELECT
  appoint_no,
  opinion_no,
  embedding_vector,
  hm_compare_mark,
  ...,
  (IF(hm_compare_mark, 1, 0) + ... + IF(hm_meal_complain_mark, 1, 0)) AS hm_true_count
FROM dataset.target_table
WHERE ARRAY_LENGTH(embedding_vector) > 0
  AND hm_compare_mark IS NOT NULL;

CREATE VECTOR INDEX IF NOT EXISTS hm_knn_embedding_idx
ON dataset.hm_knn_source(embedding_vector)
OPTIONS(index_type='IVF');

CREATE OR REPLACE TABLE dataset.hm_consensus_stage AS
WITH neighbors_raw AS (
  SELECT query.appoint_no, query.opinion_no, base.*, distance
  FROM VECTOR_SEARCH(
    TABLE dataset.hm_knn_source,
    'embedding_vector',
    TABLE dataset.hm_knn_source,
    top_k => 21,
    distance_type => 'COSINE',
    options => '{"fraction_lists_to_search":0.05}'
  )
  WHERE NOT (base.appoint_no = query.appoint_no AND base.opinion_no = query.opinion_no)
  QUALIFY ROW_NUMBER() OVER (PARTITION BY query.appoint_no, query.opinion_no ORDER BY distance) <= 20
), agg AS (...)
SELECT appoint_no, opinion_no, hm_consensus_score, hm_winner_labels, hm_winner_support
FROM scored;

MERGE dataset.target_table T
USING dataset.hm_consensus_stage S
ON T.appoint_no = S.appoint_no AND T.opinion_no = S.opinion_no
WHEN MATCHED THEN UPDATE SET
  hm_consensus_score = S.hm_consensus_score,
  hm_winner_labels = S.hm_winner_labels,
  hm_winner_support = S.hm_winner_support;
```

## Pitfalls

- Do not recompute HM consensus for rows whose HM labels are explicitly unavailable; leaving them NULL/empty is correct.
- Do not let the query row count as its own neighbor.
- Do not use the wide rebuild table as the vector-search base if a compact source table can be created; the wide table can make previews enormous and queries more expensive.
- Do not compare new UMAP coordinates from a freshly fit repair model with historical coordinates; HM consensus should use embeddings/KNN, not UMAP x/y comparability.
- If `VECTOR_SEARCH` output is very large, project only the keys/support fields needed for aggregation; avoid returning embedding arrays in query output.