# Passenger survey HM consensus: local KNN fallback

Use this reference when recomputing `hm_consensus_score`, `hm_winner_labels`, and `hm_winner_support` for a large passenger-survey row-level table and BigQuery `VECTOR_SEARCH` is too slow, gets detached from the CLI, or is killed before materializing the stage table.

## When to use

- Embeddings and UMAP are already complete.
- HM labels are available for only a subset of rows; rows where HM is unrecoverable must stay `NULL` / empty winner arrays.
- The target is large enough that a self-join KNN query may be expensive or fragile.
- Python has `pynndescent`, `numpy`, `pandas`, `google-cloud-bigquery`, and BigQuery Storage available.

## Safe sequence

1. Verify first:
   - `COUNTIF(ARRAY_LENGTH(embedding_vector)=0) = 0`
   - `COUNTIF(x IS NULL OR y IS NULL) = 0`
   - count HM-available rows with `hm_compare_mark IS NOT NULL`
   - count HM-unavailable rows separately and confirm they do not already have consensus fields filled.

2. Build a local source dataframe only for HM-available rows:
   - keys: `(appoint_no, opinion_no)`
   - `embedding_vector`
   - all 16 `hm_*_mark` fields
   - do not include rows flagged as unrecoverable HM labels.

3. Convert embeddings to a dense `float32` matrix and HM marks to a boolean matrix.
   - On a 16GB Mac, ~506k x 3072 float32 vectors are about 5.8GB before dataframe overhead; watch memory and avoid unnecessary copies.

4. Build approximate KNN with `pynndescent.NNDescent`:
   - `metric='cosine'`
   - query `k=21`, then remove self and keep 20 neighbors
   - use a fixed `random_state` for repeatability.

5. Compute HM consensus exactly like the dashboard analytics intent:
   - if the current row has no true HM labels, score/support is the fraction of neighbors with no true HM labels and winner labels are `["無標籤"]`.
   - if the current row has true HM labels, count neighbor support per HM label, restrict winners to the current row's true labels, and score/support is `max_supported_current_label_count / k`.

6. Load results into a staging table, then `MERGE` back only these fields:
   - `hm_consensus_score`
   - `hm_winner_labels`
   - `hm_winner_support`

7. Post-merge verification:
   - HM-available rows missing any of the three fields should be 0.
   - HM-unavailable rows with non-null consensus/support or non-empty winner labels should be 0.
   - embedding and UMAP missing counts should remain 0.

## Pitfalls

- A killed `bq query` wrapper does not guarantee the BigQuery job succeeded or failed. Check the staging table row count and target missing counts before deciding whether to resume, rerun, or switch strategies.
- Avoid filling unrecoverable HM rows with synthetic consensus; this makes unavailable labels look like valid low-confidence labels.
- BigQuery `VECTOR_SEARCH` may still be the best path when the index is ready and the query is stable, but for a one-off large backfill a local approximate KNN can be more controllable and easier to monitor.
