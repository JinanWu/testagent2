# Passenger survey Stage3 metrics-only dashboard snapshots

Use when the user wants to see the dashboard/front-end tree for a historical window but explicitly does **not** want the Gemini-generated summary tree.

## Pattern

1. Use the formal dashboard source table and formal snapshot table when the request is to preview the real front-end behavior:
   - source table example: `dev-cola-rd.passenger_survey_pred_dashboard.project_semantic_features`
   - snapshot table example: `dev-cola-rd.passenger_survey_pred_dashboard.opinion_tree_metrics_summary_snapshot`
2. Run Stage3 for the desired `tour_date` month/window so it builds `metrics_tree` from BigQuery rows.
3. Disable only summary generation, not metrics generation.
   - A simple local runner can import `embedding_pipeline.orchestrator` and monkey-patch `build_stage3_opinion_summary_tree` to return an API-compatible empty object such as `{"summary": "", "children": {}}`.
   - Then call `orchestrator.run_stage3(config, dry_run=False)` for each month/window.
4. Verify writes in BigQuery with aggregate fields, not raw JSON dumps:
   - `run_id`, `run_ts`, `tour_date_start`, `tour_date_end`
   - `JSON_VALUE(metrics_tree, '$.opinion_count')`
   - `JSON_VALUE(metrics_tree, '$.scored_count')`
   - `JSON_VALUE(metrics_tree, '$.head_weighted_mean')`
5. Verify the dashboard adapter, not just BigQuery:
   - Set the dashboard backend env vars to point at the formal snapshot/source tables.
   - Call the backend data loader (for example `dashboard_backend.data_loader.get_hierarchy()`) and confirm it returns latest `source.runId`, root metrics, regions, monthly trend, and at least one leaf/tour path.

## Caveats

- The Stage3 snapshot schema may still populate `summary_model` from the existing code path even when summary generation was monkey-patched off. Report this explicitly: the field can say `gemini-2.5-flash`, but no Gemini summary tree was actually generated in the metrics-only run.
- Do not describe a root-only verification as proof of the full tree. Confirm at least one nested path with product/tour leaves.
- For passenger-survey historical windows, verify taxonomy coverage separately. Unmapped rows indicate prefix/taxonomy gaps, not necessarily bad source rows.
- `JSON_QUERY_ARRAY(metrics_tree, '$.children')` may be null if `children` is an object/dictionary rather than an array; inspect/traverse according to the actual artifact shape.

## Minimal local runner shape

```python
from embedding_pipeline.config import PipelineConfig
from embedding_pipeline import orchestrator

PROJECT_ID = "dev-cola-rd"
DATASET_ID = "passenger_survey_pred_dashboard"
SOURCE_TABLE = "project_semantic_features"
REPORT_TABLE = "opinion_tree_metrics_summary_snapshot"

def empty_summary_tree(*_args, **_kwargs):
    return {"summary": "", "children": {}}

orchestrator.build_stage3_opinion_summary_tree = empty_summary_tree

cfg = PipelineConfig(
    start_date="2026-05-01T00:00:00",
    end_date="2026-05-31T23:59:59",
    project_id=PROJECT_ID,
    dataset_id=DATASET_ID,
    table_id=SOURCE_TABLE,
    full_table_id=SOURCE_TABLE,
    location="us-central1",
    api_env="production",
    stage="stage3",
    stage3_report_table_id=REPORT_TABLE,
)
orchestrator.run_stage3(cfg, dry_run=False)
```
