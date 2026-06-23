# Passenger survey dashboard tree contract notes

Use when inspecting the passenger survey dashboard tree across the producer job, backend adapter, and frontend dashboard.

## Repos / files to trace

Producer job:
- `passenger-survey-dashboard-jobs/embedding_pipeline/orchestrator.py`
- `passenger-survey-dashboard-jobs/embedding_pipeline/bigquery.py`

Backend adapter:
- `multi-agent-service/dashboard_backend/data_loader.py`
- `multi-agent-service/dashboard_backend/data_manager.py`
- `multi-agent-service/dashboard_backend/API_CONTRACT.md`

Frontend:
- `multi-agent-web/frontend/src/api/dashboard.ts`
- `multi-agent-web/frontend/src/components/dashboard/Analytics.tsx`

## Inspection sequence

1. Start at the producer job and identify the raw `metrics_tree` shape, not only comments or API docs.
   - Check prefix routing maps such as `TOUR_CODE_PREFIX_TO_LEAF`.
   - Check which fields are actually selected for Stage 3, especially `_STAGE3_SELECT_COLUMNS`.
   - Verify whether `tour_name` is present before claiming a tour-name layer exists.

2. Follow the backend adapter from BigQuery snapshot to API shape.
   - In `data_loader.py`, depth arrays such as `CHILD_KEY_BY_DEPTH` and conversion functions such as `_convert_node()` define the effective dashboard hierarchy.
   - Synthetic grouping helpers such as `_build_tour_children()` may add levels that the upstream tree did not materialize.

3. Follow the frontend types and drill stack.
   - In `dashboard.ts`, interfaces define what the frontend can consume.
   - In `Analytics.tsx`, `StackSegment`, `resolveContext()`, `getChildItems()`, and breadcrumb logic define which levels are actually navigable.

4. Compare all three layers before recommending a tree schema.
   - Producer shape, backend adapter shape, and frontend drill levels must all agree.
   - If adding a new level, list all three layers that must change.

## Durable finding from the 2026-06 inspection

The current implementation was not `大區 → 小區 → prefix → 團名 → 團號 → opinions`.

Observed implementation:
- Stage 3 producer routes `tour_code` prefix to a product/destination leaf.
- Prefix is a routing key, not a displayed node.
- Backend adapter expects fixed depths: `regions → lines → groups → products → tours`.
- If product leaf has flat `opinions`, backend groups them by `tour_code` into synthetic `tours`.
- Frontend drill stack supports only `region | line | group | product | tour`.
- The frontend tree leaf is `tour` (departure group). `guests` is not another drill-tree level; it is a detail array rendered inside the `tour` leaf page.

Current effective frontend/API leaf shape:
- `Tour`: `id`, `name`, `departDate`, `pax`, `metrics`, optional `benchmark`, `trend`, `summary`, optional `guests`.
- `Guest`: frontend type currently declares `id`, `name`, `score`, optional `room`, optional `feedback`; backend may additionally include `appoint_no`, `tour_code`, `tour_date`.
- Tour page UI renders `departDate`, `pax`, product code, then a guest score table. Existing UI only displays `guest.name`, `guest.room`, `guest.score` unless expanded-row/modal logic is added.

Restoring "click guest row to show opinion text":
1. Trace all three layers before changing code:
   - Producer metrics-tree leaf opinions: check whether `suggestion_describe` is preserved or dropped (e.g. `_METRICS_LEAF_OPINION_DROP_KEYS`).
   - Backend `_leaf_guests()`: ensure `feedback` is populated from `suggestion_describe` or fetched by `(appoint_no, opinion_no)` from the source table.
   - Frontend `Analytics.tsx`: add row click/expanded-row/modal state to display `guest.feedback`.
2. Preferred slim-tree backend approach when only the latest month needs text:
   - Keep `metrics_tree` carrying only keys/score fields.
   - In `get_hierarchy()`, after loading the latest snapshot, recursively collect `(appoint_no, opinion_no)` only from that latest `metrics_tree`.
   - Query `project_semantic_features` / `OPINION_TABLE_FQN` with a `requested_keys` CTE (`UNNEST([STRUCT(...), ...])`) joined on `CAST(appoint_no AS STRING)` and `CAST(opinion_no AS STRING)` to fetch non-empty `suggestion_describe`.
   - Pass the resulting `feedback_by_key` dict through `_convert_node()` and `_build_tour_children()` into `_leaf_guests()`; set `guest.feedback` from the dict, falling back to embedded `opinion.suggestion_describe` if present.
   - Add regression tests for key collection/de-dupe, key-join SQL shape, and `_leaf_guests()` feedback hydration. A lightweight `unittest` can stub `google.cloud.bigquery` before importing `dashboard_backend.data_loader` if the service has no test harness.
3. If keeping `suggestion_describe` inside `metrics_tree`, expect larger snapshot JSON but simplest data path.
4. If keeping `metrics_tree` slim, backend must query the opinion source table by key before building guests; document the added BigQuery cost/latency, but avoid historical full scans by collecting keys only from the latest snapshot tree.
5. Do not confuse the guest list with a new hierarchy level. It is detail data under the `tour` leaf.

Recommended target if business needs tour names:
`root → 大區/region → 次區或組別/line/group → 產品或目的地/product → tour_name → tour_code/tour → guests/opinions detail`

Implementation implication:
- Add `tour_name` to Stage 3 selected fields.
- Materialize `tour_name → tour_code` in `metrics_tree`, or add a compatible backend fallback.
- Update backend depth mapping and conversion logic to include `tourNames` between `products` and `tours`.
- Update frontend `dashboard.ts` types and `Analytics.tsx` drill stack/breadcrumb to support `tourName`.
- If only the producer adds a `tour_name` level while backend/frontend still support only `region | line | group | product | tour`, the frontend may treat `tour_name` as the terminal `tour` and fail to expose the real `tour_code`/guest leaf correctly.

Pitfall:
Do not infer a hierarchy from memory or product terminology alone. Verify the concrete tree keys, selected fields, backend depth mapping, frontend stack, and tour-page guest rendering before reporting what the dashboard can render or proposing schema changes.