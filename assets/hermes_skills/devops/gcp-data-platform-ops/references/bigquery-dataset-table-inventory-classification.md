# BigQuery dataset table inventory classification

Use when the user asks which tables in a BigQuery dataset are formal/current data vs backups vs intermediate rebuild artifacts.

## Read-only inventory pattern

1. List physical tables/objects:

```bash
bq ls --format=json --max_results=1000 PROJECT:DATASET
```

2. Summarize size/count/timestamps from `__TABLES__` and schema width from `INFORMATION_SCHEMA.COLUMNS`:

```sql
WITH meta AS (
  SELECT table_id, row_count, size_bytes,
         TIMESTAMP_MILLIS(creation_time) AS created_at,
         TIMESTAMP_MILLIS(last_modified_time) AS modified_at
  FROM `PROJECT.DATASET.__TABLES__`
  WHERE NOT STARTS_WITH(table_id, '_SEARCH_INDEX_')
), cols AS (
  SELECT table_name, COUNT(*) AS col_count
  FROM `PROJECT.DATASET.INFORMATION_SCHEMA.COLUMNS`
  GROUP BY table_name
)
SELECT table_id, row_count, ROUND(size_bytes/POW(1024,3),2) AS size_gib,
       col_count, created_at, modified_at
FROM meta LEFT JOIN cols ON table_id = table_name
ORDER BY table_id;
```

3. Check whether formal snapshot/dashboard tables point to a source table. For passenger-survey-style snapshots:

```sql
SELECT run_id, run_ts, source_table, tour_date_start, tour_date_end, summary_model
FROM `PROJECT.DATASET.opinion_tree_metrics_summary_snapshot`
ORDER BY run_ts DESC
LIMIT 5;
```

A current snapshot whose `source_table` is `project_semantic_features` is strong evidence that the formal dashboard chain is:

`project_semantic_features -> opinion_tree_metrics_summary_snapshot`

## Classification heuristics

Classify conservatively; do not delete anything from this inventory step.

- Formal/current:
  - canonical business names with no suffix, e.g. `project_semantic_features`, `opinion_tree_metrics_summary_snapshot`
  - tables referenced by current snapshot metadata or production code/config
- Backup:
  - names containing `backup`, `pre_*_replace`, or explicit timestamped rollback language
  - often full row-count copies or targeted pre-replacement subsets
- Intermediate/rebuild staging:
  - names containing `stage`, `missing`, `chunk`, `worker`, `_w00`, `knn_source`, `audit`, `rebuild`, `backfill`
  - may include `*_final`; treat as a rebuild output unless current code/snapshot points to it directly
- System/internal:
  - `_SEARCH_INDEX_*` / vector-search index backing tables; do not present them as business data tables

Example classifier query:

```sql
WITH meta AS (
  SELECT table_id, row_count, size_bytes
  FROM `PROJECT.DATASET.__TABLES__`
), classified AS (
  SELECT
    CASE
      WHEN STARTS_WITH(table_id, '_SEARCH_INDEX_') THEN 'system_index'
      WHEN table_id IN ('project_semantic_features', 'opinion_tree_metrics_summary_snapshot') THEN 'formal_current'
      WHEN REGEXP_CONTAINS(table_id, r'backup|pre_.*replace') THEN 'backup'
      WHEN REGEXP_CONTAINS(table_id, r'stage|missing|chunk|knn_source|audit|rebuild|backfill|_w[0-9][0-9]$') THEN 'intermediate_rebuild'
      ELSE 'needs_confirmation'
    END AS category,
    *
  FROM meta
)
SELECT category, COUNT(*) AS table_count, SUM(row_count) AS total_rows,
       ROUND(SUM(size_bytes)/POW(1024,3), 2) AS total_gib
FROM classified
GROUP BY category
ORDER BY category;
```

## Report shape

Keep the answer operational:

- state project/dataset and that the check was read-only
- give counts by category: formal/current, backup, intermediate/rebuild, system index
- list formal/current tables first with row counts and what they are used for
- list backups separately from stage/intermediate tables
- explicitly call out `*_final` tables as not necessarily formal unless referenced by current consumers
- for large tables, provide aggregate quality checks only and 2-3 sample rows if needed

## Pitfalls

- Do not infer that a `*_final` table is formal just from the name; verify snapshot/source references or code consumers.
- Do not count `_SEARCH_INDEX_*` objects as user/business tables.
- Do not recommend deletion from naming heuristics alone; first preserve an inventory export and verify no current queries/jobs reference the table.
