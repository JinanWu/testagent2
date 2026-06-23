# Passenger-survey dashboard trend API debugging

Use when the dashboard shows `歷史趨勢暫時載入失敗` or a trend chart is empty while BigQuery appears populated.

## Fast triage sequence

1. Verify the frontend's actual backend target, not only the repo you expect to be current.
   - For Vite proxy setups, inspect `vite.config.ts` and any `VITE_API_BASE_URL` override.
   - Check the listening backend PID and cwd, e.g. `lsof -nP -iTCP:8000 -sTCP:LISTEN` then `lsof -p <pid> | awk '$4=="cwd" {print $9}'`.
   - A stale uvicorn process from another worktree can return old API behavior even when the current repo contains the right endpoint.

2. Compare endpoint behavior on the live backend:
   - `/dashboard/api/v1/satisfaction/hierarchy`
   - `/dashboard/api/v1/satisfaction/trend?node_type=root&node_id=root`
   - `/openapi.json` to confirm whether trend routes are mounted.

3. Verify BigQuery separately before blaming storage:
   - `opinion_tree_metrics_summary_snapshot` should have monthly rows with `tour_date_start`, `tour_date_end`, `run_ts`, `metrics_tree`, `summary_tree`.
   - `project_semantic_features` should have source rows and non-null `ai_sentiment_score` for the relevant months.
   - Extract root scores from snapshot JSON with `JSON_VALUE(metrics_tree, '$.level_weighted_mean')` and `JSON_VALUE(metrics_tree, '$.head_weighted_mean')` rather than expecting frontend-shaped keys like `$.metrics.passenger` inside the stored tree.

## Common root causes

- Latest code has the trend endpoint, but the frontend is pointed at an older backend process on the expected port.
- Backend snapshot selection filters or ordering exclude the newest row, e.g. hard-coded summary model assumptions. Treat `gemini-2.5-flash-lite` as a valid summary model if that is what the current snapshot uses.
- Initial hierarchy intentionally strips full trend arrays for page-load speed; frontend then lazy-loads trend via `/satisfaction/trend`. If that endpoint is missing on the live backend, the UI error appears even though BigQuery has history.

## Verification pattern

Start the current backend on an alternate free port without disturbing the user's existing server:

```bash
cd '<task>/multi-agent-service/backend'
PYTHONPATH='../:.' python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Then compare:

```bash
curl -sS 'http://127.0.0.1:8001/dashboard/api/v1/satisfaction/trend?node_type=root&node_id=root'
curl -sS 'http://127.0.0.1:8001/dashboard/api/v1/satisfaction/hierarchy?force_reload=true'
```

Expected healthy trend response: HTTP 200 with non-empty `monthly` and `yearly` arrays. If 8001 is healthy while 8000 returns 404, the issue is runtime/process routing, not BigQuery or the current source code.
