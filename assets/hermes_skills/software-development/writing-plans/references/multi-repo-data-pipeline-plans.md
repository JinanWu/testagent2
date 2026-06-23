# Multi-repo data pipeline planning reference

Use this reference when writing implementation plans for features that span ETL / warehouse / backend / frontend repos.

## Required reconnaissance

- Identify every repo involved and record the absolute paths.
- Check branch + worktree status for each repo before planning implementation tasks.
- Call out any existing uncommitted files that future implementers must not overwrite.
- Find the source-of-truth schema and the downstream API/UI type definitions.
- Trace the data path end-to-end: upstream API/files -> serialization -> warehouse schema -> aggregation/snapshot -> backend loader -> frontend types/rendering.

## Plan structure additions

Add these sections when relevant:

1. Current repo state
   - Repo path, branch, dirty files.
   - Base branch expectation for PRs.

2. Data model proposal
   - Warehouse DDL.
   - Serialized record shape.
   - Backend API shape.
   - Frontend TypeScript shape.
   - Stable identifiers vs display names.

3. Backfill / migration strategy
   - Prefer safe, idempotent scripts.
   - Add `--dry-run` and `--execute` modes.
   - Update only missing/target columns; never touch embeddings, labels, or unrelated derived fields.
   - Print before/after coverage and conflict counts.
   - Verify mapping key uniqueness; if display names conflict for the same key, stop and require a tie-break rule.

4. Snapshot/regeneration step
   - If downstream dashboards read materialized snapshots, explicitly schedule snapshot regeneration after rowdata backfill.
   - Existing snapshots do not update automatically after base-table changes.

5. Backend/frontend compatibility
   - Preserve stable IDs/deep-link keys; add new display fields separately.
   - Keep frontend changes minimal when backend can return existing `name` fields populated with better display values.

## Common pitfall

Do not stop at adding a warehouse column. For dashboard data, plan the full propagation path and validation: rowdata coverage -> aggregation/snapshot contains field -> backend response contains field -> frontend renders field.