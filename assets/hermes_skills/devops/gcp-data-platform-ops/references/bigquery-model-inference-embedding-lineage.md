# BigQuery model-inference backfill: embedding lineage checks

Use this note when a BigQuery repair/backfill uses stored vectors to feed an offline ML model.

## Durable lesson

Do not assume a column named `embedding_vector` is the same embedding the inference model was trained on. Verify:

1. Vector dimension in the candidate rows:
   - `SELECT ARRAY_LENGTH(embedding_vector) AS dim, COUNT(*) ... GROUP BY dim`
2. The classifier/model input dimension from config, code, or checkpoint:
   - examples: `INPUT_DIM`, first linear layer shape, sklearn `n_features_in_`, model config JSON.
3. Embedding model lineage:
   - source model name, task type, location, output dimension, and whether the table was later rebuilt for semantic search/dashboard use.

## If stored embedding does not match

Prefer one of these patterns:

- Regenerate the classifier’s expected embedding from source text inside each worker, then immediately run inference and stage the labels.
- Or create a separate temporary stage table for the classifier embedding, with explicit naming such as `_legacy_768_embedding_stage`, then run inference from that stage.

Avoid forcing/truncating/padding vectors unless the model owner confirms that transformation was used during training.

## Operational pattern

- Keep deterministic worker sharding and per-worker stage tables.
- Write only inference outputs needed for the final MERGE; avoid carrying large repeated vector columns through worker query result pages if they are not used.
- For local workers on a MacBook, start with process tracking and `notify_on_complete`; also configure an external progress cron if the user wants chat updates.
- Progress report should include: process id, stage rows by worker, total/missing percentage, whether MERGE happened, recent errors/Tracebacks, and ETA.

## Example failure signature

A PyTorch/sklearn classifier expecting 768 features fails when fed a table vector rebuilt as 3072 dimensions:

`ValueError: features shape must be [n,768], got (batch, 3072)`

Resolution: verify the old classifier embedding model, regenerate that 768-dimensional embedding from the row text, and keep the dashboard/semantic-search embedding untouched.