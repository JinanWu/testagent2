# Hierarchy drill-down debugging notes

Use this pattern when a dashboard/tree UI seems to stop early or inconsistently shows `group -> leaf` on some branches and `group -> product -> tour` on others.

## What to verify

1. Confirm the live API response shape, not just the code contract.
   - Check the exact keys present at each level.
   - Confirm the deepest level exists in the returned JSON.

2. Count how many branches actually have children.
   - A missing next-click is often a leaf node with no children, not a field-name bug.
   - Quantify both:
     - branches with children
     - branches without children

3. Compare identifiers, not just display names.
   - Verify the UI looks up children by the same `id`/key the API emits.
   - If names are used as labels, ensure they are not also being used as lookup keys accidentally.
   - When a frontend marker/link has been changed to a normalized or virtual id, fetch the live API response and confirm the backend is already serving that exact id. A UI can look empty if the frontend sends `歐洲郵輪 / 歐洲` but the live API still returns only `歐洲郵輪`.

4. Confirm the running backend actually picked up the code change.
   - In local multi-package apps, `uvicorn --reload` may only watch the current working directory; sibling packages mounted via `PYTHONPATH=..` may not reload.
   - If the modified file lives outside the reload watch root, restart the backend or run with an explicit reload dir such as `--reload-dir ..` before judging the fix.
   - Treat this as a frontend/backend version mismatch until proven otherwise: compare compiled frontend constants, live API ids, and the running server command/reload roots.

5. Trace the frontend data flow.
   - Confirm the fetched API data reaches the render path.
   - Check for fallback/demo data that can mask a partial success.
   - Verify the component that handles the click event only enables the next level when children exist.

5. Distinguish true schema mismatch from natural sparsity.
   - If some groups/products have children and others do not, the UI should expose that clearly.
   - A "stuck" drill-down can simply mean the selected branch is terminal.

## Session-specific evidence pattern

In the passenger-survey dashboard drill-down case:
- the API response used `regions -> lines -> groups -> products -> tours -> guests`
- BigQuery snapshot layers stored `metrics_tree` / `summary_tree` JSON, not a `rowdata` field
- the tree was sparse by design:
  - some groups are terminal leaves
  - some products are terminal leaves
  - some branches continue to tours and guests
- sampled counts showed a mix of branch and leaf nodes, so "stops early" can be normal on a per-branch basis
- a path like `日本 / 關東東北沖繩組 / 東北地區` was already terminal at the group level
- the frontend lookup keys matched the API keys; the issue was structural sparsity, not a name mismatch
- when leaf nodes already carried flat opinions, a minimal backend fix could synthesize missing intermediate nodes using `tour_code` instead of forcing every branch to be fully populated upstream

## Minimal-fix decision rule

Before changing the UI or upstream ETL:
1. Verify whether the branch is actually terminal in the source tree.
2. If it is terminal, do not invent children.
3. If the leaf still has flat opinions that belong to a deeper level, synthesize only the missing intermediate nodes needed for drill-down.
4. Keep original aggregates intact; do not recompute metrics unless the upstream contract is wrong.

## Reporting format

When summarizing this kind of issue, report:
- the deepest level present in the API
- how many parent nodes have children at that level
- how many are terminal nodes
- whether the frontend lookup key matches the API key
- whether fallback/demo content is masking real data
