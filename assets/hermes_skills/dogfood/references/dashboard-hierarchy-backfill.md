# Dashboard hierarchy backfill note

Use this when a dashboard drill-down stalls at a higher level (for example, product) and you suspect the missing depth is in the data layer rather than the click handler.

What happened in this session:
- The dashboard UI could drill down several levels, but stopped before reaching the deepest product area.
- Inspection showed the backend hierarchy was missing `tour` children under `product` nodes.
- The raw rowdata did contain a stable grouping key (`tour_code`) and could be aggregated into a deeper hierarchy.

Verification pattern:
1. Inspect the hierarchy/snapshot payload first, not just the UI behavior.
2. Confirm whether the deepest visible node has children in the API response.
3. Check the raw fact table for a grouping key that can materialize the missing level.
4. If the source snapshot lacks the deeper level, treat it as a data/ETL issue and not a frontend-only bug.

Implementation notes from the session:
- A backend data-loader can inject the missing child level from rowdata-derived aggregation.
- If the dashboard reads from a BigQuery snapshot, a one-off dev backfill can make the issue visible immediately while the upstream ETL is being updated.
- When writing BigQuery rows that contain nested tree fields, serialize nested JSON structures explicitly if the insert API expects strings rather than records.
- Prefer deriving new hierarchy labels from existing stable keys (here: `tour_code`) when no canonical name exists yet.

Reporting guidance:
- Distinguish "clicks stop because data stops" from "clicks stop because the UI is broken".
- Include the API/snapshot evidence and any backfill or ETL gap in the final bug report.
