# BigQuery dashboard variable-depth tree debugging

Use when a BigQuery-backed dashboard has source rows and a generated hierarchy, but the UI stops drilling down or appears to have data only at a higher level.

## Failure shape

- BigQuery source/snapshot counts prove rows exist for the selected path.
- The adapter returns a nested node for that path, but the node is a leaf at a depth earlier than the front-end expects, or it still has children after the adapter's fixed depth limit.
- The front-end drilldown code assumes a fixed schema such as `region > line > group > product > tour > guests` and only reads the next child collection for that exact level (`products`, `tours`, etc.).
- A taxonomy or Stage3 metrics tree may be variable-depth:
  - too shallow: a `group` or `product` node directly carries `guests`/`opinions` without a `products`/`tours` child collection;
  - too deep: an extra taxonomy layer (for example `歐洲郵輪 / 歐洲 / ...`) pushes `tour_name -> tour_code -> opinions` one level deeper than the adapter/front-end contract, so nodes at the fixed depth limit have children that are silently not exposed.

## Debug sequence

1. Pick one failing click path and verify source counts by each path component.
   - Count by region/line/group/product-like labels.
   - Sample only 2-3 `tour_code`/`tour_name` rows.
2. Inspect the generated Stage3 taxonomy/metrics tree for that path.
   - Confirm whether the node has children, direct opinions, direct guests, or both.
   - Record leaf depth distribution across the whole tree, not just the failing path.
   - Also count nodes that still have `children` at or beyond the adapter's maximum fixed depth; these are the mirror image of early leaves and often explain “data disappears below tour name.”
3. Inspect the dashboard adapter output for the same path.
   - List node keys (`products`, `tours`, `guests`, `opinions`, metrics fields).
   - Distinguish "no data" from "data attached at a level the front-end ignores".
4. Verify the browser action against the live API response, not just the edited code.
   - Compare the id emitted by the UI marker/link with the ids in `/satisfaction/hierarchy`.
   - If the frontend now sends a virtual/normalized path such as `歐洲郵輪 / 歐洲`, the live API must already expose a region with that exact id; otherwise `resolveContext`-style lookup code will fall back to the root/company context and render an apparently empty drilldown.
5. Inspect the frontend drilldown conditions.
   - Identify which exact child property each UI level reads.
   - If a non-tour level has direct `guests`, check whether the UI ever renders them.
6. In local development, confirm reload boundaries before concluding the fix failed.
   - A FastAPI app launched from `backend/` with `PYTHONPATH=.. uvicorn app.main:app --reload` may not watch sibling packages such as `../dashboard_backend/`.
   - After changing dashboard adapter code outside the cwd, restart the server or use `--reload-dir ..`; then refetch the live hierarchy JSON.
   - Record the server command, cwd, and live API ids in the bug report so a stale backend is not confused with a data-shape failure.

7. Report root cause as a contract mismatch when applicable:
   - Stage3/taxonomy emits a variable-depth tree.
   - The adapter preserves leaf data at arbitrary depths.
   - The front-end expects a fixed-depth tree and therefore stops before showing valid leaf data.

## BigQuery snapshot probe for too-deep leaves

When the adapter has a fixed child-key list such as `['regions', 'lines', 'groups', 'products', 'tours']`, scan the stored `metrics_tree` for nodes with children at or beyond that limit. Those nodes can carry valid lower-level data in BigQuery while the API returns only metrics/summary for the parent.

Minimal Python pattern after exporting one snapshot row to JSON:

```python
import json
row = json.load(open('/tmp/latest_metrics.json'))[0]
metrics = json.loads(row['metrics_tree'])
fixed_keys = ['regions', 'lines', 'groups', 'products', 'tours']
problem = []


def walk(node, path=()):
    children = node.get('children') or {}
    if children and len(path) >= len(fixed_keys):
        problem.append((path, node.get('opinion_count'), len(children), list(children)[:3]))
    for name, child in children.items():
        walk(child, path + (name,))

walk(metrics)
for path, count, child_count, sample in problem[:20]:
    print(len(path), '/'.join(path), count, child_count, sample)
```

Interpretation example: if all problem nodes are under `歐洲郵輪` and paths look like `歐洲郵輪/歐洲/<group>/<leaf>/<tour_name>` with child samples that are real `tour_code`s, BigQuery has the data and the bug is the adapter/front-end fixed-depth contract.

## Repair options

Prefer making the API/adapter contract match the existing front-end if the UI is already built around fixed levels:

- Adapter-side normalization: when a variable-depth leaf has direct opinions/guests before the expected tour level, synthesize a stable next layer (for example group leaf `三城全覽` -> tour_code node -> guests). This keeps the front-end drilldown contract unchanged.
- Adapter-side pass-through/collapse for too-deep trees: when a node still has children after the fixed depth limit, either continue converting those children under the closest supported child key or collapse a known redundant taxonomy layer before conversion. Verify that real `tour_code` leaves still become `guests`.
- Fixed-contract normalization for known redundant layers: if the UI contract is already `regions -> lines -> groups -> products -> tours -> guests`, do **not** generalize the API to arbitrary depth as the first fix. Instead, collapse only the known redundant layer (for example a taxonomy shape like `歐洲郵輪 / 歐洲 / <line> / <group> / <tour_name> / <tour_code>` should usually become `歐洲郵輪 / <line> / <group> / <tour_name> / <tour_code>` before adapter conversion). Apply the same path transform to `metrics_tree` and `summary_tree` so summaries still resolve.
- Upstream taxonomy normalization: remove redundant taxonomy layers before writing the snapshot when the business hierarchy should be fixed-depth; prefer this when historical trend/table replacement is already part of the repair.
- Front-end generalization: allow any level with direct `guests`/leaf records to render those records, not only `tour` nodes. This is more flexible but changes UI semantics across pages and should be a last resort when the product truly requires variable-depth hierarchy.

## Reporting pattern

For the failing path, include:

- Source-row counts and 1-3 representative tour codes/names.
- Stage3 taxonomy meaning for the leaf (`[]`, direct opinions, or nested children).
- Adapter node keys for the path.
- Front-end condition that ignores the available data.
- A recommendation: adapter normalization vs front-end generalized leaf rendering.

## Root category missing from UI entrypoint

If BigQuery root children contain a category but the dashboard home screen does not show it, do not stop at the warehouse/backend checks. Verify the actual UI entrypoint list as well:

1. Confirm the snapshot root has the category: inspect `metrics_tree.children` names and counts.
2. Confirm the backend adapter includes all `root_children` in `regions` and does not filter the category.
3. Inspect the frontend entrypoint component for hardcoded region/marker arrays (for example `REGIONS` in a globe/map component). A category can be present in the API but invisible if the home visualization only renders hardcoded markers.
4. Prefer a minimal fix that keeps the API contract stable: add the missing category marker, or better, drive visible markers from API `regions` plus a separate coordinate/display config. Avoid changing hierarchy shape just to expose a missing root entry.

Report this as "UI entrypoint omitted the category" rather than "BigQuery has no data" when the API contains the root node but the frontend marker/list does not.

## Virtual root aliases for split UI entrypoints

If the UI intentionally splits/renames root entrypoints differently from the stored BigQuery taxonomy, treat that as a precise entrypoint alias rather than a general variable-depth hierarchy change. Build adapter entries as `(display_name, node, source_path)` so the UI can show the desired label while summary/trend continue to resolve against the original stored path. Frontend marker/list ids must match the API node id derived from `source_path`, not necessarily the display label. Hide only the redundant old root entry. See `references/bigquery-dashboard-virtual-root-entrypoint.md` for the detailed pattern.

## Pitfalls

- Do not conclude "BigQuery has no data" from a UI drilldown stop; check source table, snapshot JSON, adapter output, and front-end conditions separately.
- Do not verify only the root or first two levels of a dashboard tree. Variable-depth leaf bugs often appear at lower levels.
- Do not assume variable-depth means only "too shallow"; extra taxonomy layers can make data too deep for a fixed adapter and hide valid child nodes below the fixed depth limit.
- Do not assume `children` is always an array; Stage3 artifacts may use object/dictionary children.
- Do not call a node `tour` only because it is a leaf; verify whether it has a real tour identifier/name or is just a taxonomy label leaf.
