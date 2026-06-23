# Dashboard lazy trend loading after fast hierarchy

Use when the 心情指數儀表板 right-side trend chart or MoM/YoY pills show empty values after `/dashboard/api/v1/satisfaction/hierarchy` was optimized for first-load speed.

## Symptom

- Right-bottom trend card shows title, 月/年 tabs, and legend but no SVG paths/circles.
- Top KPI pills show `月 → —` / `年 → —` even though historical data exists.
- Drill-down child cards show `較上月 — 0.0` for passenger/route even when historical trend exists.

## Root-cause pattern

The fast hierarchy endpoint intentionally avoids history work:

- `data_loader.get_hierarchy_initial()` uses `empty_history = []` and `root_trend = {"monthly": [], "yearly": []}`.
- `/api/v1/satisfaction/hierarchy` returns current metrics and tree only.
- `/api/v1/satisfaction/nodes/{node_type}/{node_id}/trend` or a query equivalent can still return monthly/yearly history.

So the database can be correct while the UI has no trend data because the frontend still reads `ctx.trend` / `item.metrics.mom` from the fast hierarchy payload.

## Verification probes

Backend:

```bash
curl -sS 'http://127.0.0.1:8000/dashboard/api/v1/satisfaction/hierarchy' \
  | python3 - <<'PY'
import json, sys
d=json.load(sys.stdin)
print('root trend', {k: len((d.get('trend') or {}).get(k) or []) for k in ['monthly','yearly']})
print('first child metrics', d['regions'][0]['name'], {k:d['regions'][0]['metrics'].get(k) for k in ['mom','routeMom','yoy','routeYoy']})
PY

curl -sS 'http://127.0.0.1:8000/dashboard/api/v1/satisfaction/trend?node_type=root&node_id=root' \
  | python3 - <<'PY'
import json, sys
d=json.load(sys.stdin)
print('trend monthly', len(d.get('monthly') or []), 'yearly', len(d.get('yearly') or []))
print('last', (d.get('monthly') or [])[-1])
PY
```

Browser DOM:

```js
const art = [...document.querySelectorAll('article')]
  .find(e => e.getAttribute('aria-label') === '心情指數趨勢分析')
({
  svgCount: art?.querySelectorAll('svg').length,
  pathCount: art?.querySelectorAll('path').length,
  circleCount: art?.querySelectorAll('circle').length,
  text: document.body.innerText,
})
```

Expected after fix: trend card has `svgCount >= 1`, paths/circles, and KPI/child-card deltas are no longer `—` when the corresponding current score is present.

## Implementation pattern

1. Keep `/satisfaction/hierarchy` fast.
2. Add/use a lazy trend fetch from the frontend after hierarchy loads.
3. Prefer a query-string trend endpoint for node IDs containing `/`:

   ```http
   GET /dashboard/api/v1/satisfaction/trend?node_type=region&node_id=歐洲郵輪%20%2F%20歐洲
   ```

   Keep the existing path endpoint for backward compatibility.

4. Add frontend in-flight de-dupe by trend cache key (`nodeType:nodeId`) because React StrictMode can double-run effects in development.
5. Display a local loading state inside the chart area, not a page-wide spinner.
6. Use the returned `monthly` history to derive display deltas:
   - `mom`: latest valid passenger minus previous valid passenger
   - `yoy`: latest valid passenger minus 12th previous valid passenger
   - `routeMom`: latest valid route minus previous valid route
   - `routeYoy`: latest valid route minus 12th previous valid route
7. Apply derived deltas to all places that display deltas:
   - top KPI cards for the current selected context
   - right-bottom trend chart
   - visible child/drill cards (`較上月`)
8. Do not apply a derived delta when the corresponding current score is missing/gray (`0`, `null`, `undefined`, non-finite). Otherwise the UI can show `旅客平均心情 -` but `較上月 ▲`, which is inconsistent.
9. Refresh should clear trend cache and trigger a new lazy trend fetch. Use a refresh nonce/tick if the selected node identity does not change.

## Common pitfalls

- Fixing only the right-bottom SVG leaves KPI MoM/YoY pills stale.
- Fixing only the top KPI leaves drill-card `較上月` stale because `DrillCard` reads each `item.metrics.*Mom` directly.
- Path-style URLs cannot safely represent node IDs that include `/`; use query parameters for lazy trend fetches.
- Full hierarchy still computes trends but can be slow; do not regress first-load speed by switching the main page back to full hierarchy.
- If a child score is treated as missing/gray, preserve neutral delta display even if historical trend has values.
