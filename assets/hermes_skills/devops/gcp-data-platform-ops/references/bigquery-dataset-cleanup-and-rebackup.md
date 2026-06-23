# BigQuery dataset cleanup and immediate re-backup pattern

Use when the user asks to delete backup/stage/temp tables from a BigQuery dataset and keep only formal tables.

## Safe workflow

1. Re-inventory the dataset immediately before deletion.
   - Query `__TABLES__` for `table_id`, row counts, size, creation and modified timestamps.
   - Query `INFORMATION_SCHEMA.COLUMNS` for schema column counts.
   - Use `bq ls` after cleanup because hidden/system objects may behave differently from `__TABLES__`.
2. Define explicit keep-list first, not delete-list first.
   - Formal production/current tables must be exact table names.
   - Also protect BigQuery system/index objects such as `_SEARCH_INDEX_*`; do not classify them as user backup/temp data.
3. Classify deletion candidates by naming pattern only after the keep-list is fixed.
   - Common delete patterns: `backup`, `pre_*_replace`, `stage`, `missing`, `chunk`, `knn_source`, `audit`, `rebuild`, `backfill`, worker suffixes like `_w00`.
   - Treat `*_final` carefully: it can look official but is often a rebuild handoff table; verify whether live consumers point at it before deleting.
4. Run a guard script that aborts if any protected table or `_SEARCH_INDEX_*` object appears in the delete list.
   - Print counts: total objects, delete candidates, keep objects, unknown objects.
   - Persist inventory and delete results to local JSON logs.
5. Delete one table at a time with `bq rm -f -t project:dataset.table`.
   - Stop on first unexpected error rather than continuing blindly.
6. Verify after deletion.
   - `bq ls` should show only intended formal tables.
   - Re-query formal row counts and key date ranges.
7. If the user wants new backups after cleanup, create fresh backups from the formal tables with `bq cp` and a timestamp suffix.
   - Verify backup row counts and schema column counts match the source.

## Reporting shape

Keep the final report concise and operational:
- Project/dataset.
- Delete count and success count.
- Explicit formal tables preserved.
- Post-cleanup `bq ls` result count.
- Formal row counts/date ranges.
- New backup table names, row counts, and column counts if created.
- Local inventory/result log paths when available.

## Pitfalls

- Do not delete based only on broad regexes without an exact protected keep-list.
- Do not assume BigQuery `_SEARCH_INDEX_*` objects are removable business tables.
- Do not treat a table named `*_final` as current unless downstream/source references confirm it.
- After deleting staging/source tables, search/index objects may disappear from listings without direct deletion; report that separately from user-table deletions.
