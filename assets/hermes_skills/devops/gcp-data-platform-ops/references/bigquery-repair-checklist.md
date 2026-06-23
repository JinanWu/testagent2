# BigQuery repair checklist

- Confirm project, dataset, table, and schema first.
- Measure the target window and key coverage before any write.
- Create a backup before repair operations.
- Validate the repair source and join keys; do not guess.
- For large vector-search repairs, search the full base corpus but chunk only the query/missing rows to reduce memory pressure; stage chunk outputs before the final merge.
- Re-run counts and spot-check repaired rows after the write.
