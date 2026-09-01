# Passenger-survey sparse tree backfill

Use this when a dashboard/tree looks like it should drill down further, but some nodes are intentionally terminal in the upstream snapshot.

## What we learned
- The source opinion table is flat rows; it does not contain prebuilt group/product hierarchy columns.
- The reporting snapshot stores `metrics_tree` and `summary_tree` JSON, not `rowdata`.
- For tree-backed dashboards, a leaf is not automatically a bug. Some branches are legitimately sparse.
- When the product requirement is "make drill-down feel normal", the fix may need to happen in the ETL/write path, not only in the dashboard API read path.

## Verification pattern
1. Inspect the upstream tree shape first.
2. Count branch-vs-leaf nodes by depth.
3. Pick a few known paths and verify whether they are terminal by design.
4. If a terminal node still has flat records beneath it, reconstruct missing intermediate nodes from existing tree structure plus a stable grouping key such as `tour_code`.
5. Write the corrected tree back to the snapshot table and verify the newest `run_id`/`run_ts` is actually the corrected one.

## Practical rules
- Do not assume missing children means bad data.
- Do not invent business names that do not exist upstream; use a conservative synthetic label only when you are explicitly filling structure.
- Preserve existing aggregate metrics when synthesizing intermediate nodes unless you have a verified aggregation rule.
- Always verify both the served API shape and the stored BigQuery snapshot.

## Useful checks
- Confirm the latest snapshot row still has non-zero `scored_count`.
- Compare `opinion_count` vs `scored_count` before and after rewrite.
- Check a few representative paths to make sure the expected child keys appear at each level.
- If the fix only changes frontend drill-down behavior, it is incomplete when the requirement is to correct the stored tree.
