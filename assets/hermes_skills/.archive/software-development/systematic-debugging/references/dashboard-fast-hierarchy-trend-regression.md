# Dashboard fast hierarchy trend regression

Use when the 心情指數儀表板 right-side `心情指數趨勢分析` card shows only the title/tabs/legend but no line chart.

## Symptom

- Frontend renders the trend card (`aria-label="心情指數趨勢分析"`) but `svg`, `path`, and `circle` counts inside the card are zero.
- Browser resource timing shows only `GET /dashboard/api/v1/satisfaction/hierarchy` and no `GET /dashboard/api/v1/satisfaction/nodes/{node_type}/{node_id}/trend`.
- `TrendChart` receives `trend.monthly = []` and `trend.yearly = []`, so it returns an empty placeholder div.

## Root-cause probe

Compare the fast hierarchy response with the node-specific trend endpoint:

```bash
curl -sS 'http://127.0.0.1:8000/dashboard/api/v1/satisfaction/hierarchy?force_reload=true' > /tmp/hierarchy.json
python3 - <<'PY'
import json
h=json.load(open('/tmp/hierarchy.json'))
print('root monthly/yearly', len(h.get('trend',{}).get('monthly') or []), len(h.get('trend',{}).get('yearly') or []))
r=(h.get('regions') or [{}])[0]
print('first region', r.get('name'), len((r.get('trend') or {}).get('monthly') or []), len((r.get('trend') or {}).get('yearly') or []))
PY

curl -sS 'http://127.0.0.1:8000/dashboard/api/v1/satisfaction/nodes/root/root/trend' > /tmp/root-trend.json
python3 - <<'PY'
import json
t=json.load(open('/tmp/root-trend.json'))
print('trend monthly/yearly', len(t.get('monthly') or []), len(t.get('yearly') or []))
print('first/last', (t.get('monthly') or [])[:1], (t.get('monthly') or [])[-1:])
PY
```

Interpretation:

- If hierarchy trend arrays are empty but node trend has rows, BigQuery data exists; the missing chart is a contract mismatch.
- In this project, the fast-load change routed `/api/v1/satisfaction/hierarchy` to `load_hierarchy_initial()`, which intentionally uses `empty_history = []` and `root_trend = {"monthly": [], "yearly": []}` to keep first load fast.
- The frontend must either lazy-load `/satisfaction/nodes/{node_type}/{node_id}/trend` for the active context or the backend must reintroduce a cheap trend payload. Do not diagnose this as a chart rendering bug or missing BigQuery data until this API comparison is done.

## Git history clue

When investigating when it disappeared, check blame/log around:

- `dashboard_backend/server.py` route `api_satisfaction_hierarchy`
- `dashboard_backend/data_loader.py` `get_hierarchy_initial()`

A fast-hierarchy speedup commit may have deliberately removed historical trends from the initial hierarchy payload while leaving the frontend still dependent on `ctx.trend` from that payload.
