# Passenger-survey dashboard local feature verification

Use when verifying small UI/API changes in the 心情指數儀表板 (`multi-agent-service` + `multi-agent-web`) without deploying.

## Run locally against real dev BigQuery

Use this workflow for this dashboard project by default. Do **not** attempt Cloud Build or Cloud Run deployment during feature verification unless the user explicitly authorizes deployment; the expected path is local backend/frontend execution and local HTTP timing checks only.

Before starting servers, check whether ports are already occupied and confirm the processes belong to the current task workspace, not an older clone:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN || true
lsof -nP -iTCP:5173 -sTCP:LISTEN || true
ps -p <PID> -o pid,ppid,command
lsof -p <PID> | grep cwd || true
```

If port 8000 is already occupied, do not reflexively kill/restart it. First verify the listening process `cwd` is the current task repo's `multi-agent-service/backend`, then probe `/health`. If both match, reuse it and report the existing listening PID. If old local services from another workspace are occupying the ports, stop only those processes, then start the current repo's backend/frontend.

Backend from `multi-agent-service/backend`:

```bash
APP_ENV=development \
DEBUG=true \
GCP_PROJECT_ID=dev-cola-rd \
DASHBOARD_BQ_PROJECT=dev-cola-rd \
DASHBOARD_BQ_DATASET=passenger_survey_pred_dashboard \
PYTHONPATH=/Users/wujinan/Documents/passenger-survey-data-fix-20260522/multi-agent-service:/Users/wujinan/Documents/passenger-survey-data-fix-20260522/multi-agent-service/backend \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend from `multi-agent-web/frontend`:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173
```

Open the browser at `http://localhost:5173/dashboard` (prefer `localhost`, not `127.0.0.1`, because this app's development CORS allowlist includes `http://localhost:5173`).

Default login is usually `admin` / `admin123` when the local SQLite DB was initialized by the app.

## Backend probes

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/dashboard/api/v1/opinions/label-definitions
/usr/bin/time -p curl -sS -o /tmp/local-hierarchy.json \
  -w 'http=%{http_code} total=%{time_total} bytes=%{size_download}\n' \
  'http://127.0.0.1:8000/dashboard/api/v1/satisfaction/hierarchy'
python3 - <<'PY'
import json, os
p='/tmp/local-hierarchy.json'
data=json.load(open(p))
print('file_bytes', os.path.getsize(p))
print('regions', len(data.get('regions') or []), 'source', data.get('source'))
PY
```

For opinion-search date changes, probe a known populated month if the current-month default has no data:

```bash
curl -s 'http://127.0.0.1:8000/dashboard/api/v1/opinions?start_date=2026-05-01&end_date=2026-05-31&page=1&limit=3'
```

Verify `total`, representative `date` values, and whether `tour_date` / `create_time` explain the displayed date.

## Initial hierarchy API speed debugging

When the task is to accelerate first load of 旅線分析, measure the deployed/current API before changing code. A useful baseline shape is:

```bash
/usr/bin/time -p curl -k -sS --max-time 180 \
  -o /tmp/hierarchy.json \
  -w 'http=%{http_code} ttfb=%{time_starttransfer} total=%{time_total} bytes=%{size_download}\n' \
  '<frontend-or-local-origin>/dashboard/api/v1/satisfaction/hierarchy'
```

If the first-load API is over 5 seconds, split backend timing before proposing fixes:
- `_latest_snapshot()` time: latest snapshot query.
- `_snapshot_history()` time: historical trend query.
- `_feedback_by_opinion_keys()` time and batch count: full-text opinion backfill.
- final JSON byte size.

The recurring root-cause pattern observed here: full hierarchy first load spent ~25–27s because it performed one latest-snapshot query, one expensive history query (~13s), and many feedback backfill queries (~29 batches, ~11–12s), then returned ~7MB. A successful local fix was to keep the full `get_hierarchy()` available but route `/satisfaction/hierarchy` to an initial fast loader that reads only the latest snapshot and skips history trends plus all-opinion full-text backfill for the first screen. Verify locally that the endpoint is under 5s, returns `regions`, and preserves `source` metadata.

## Browser verification for “tab switch should not refetch hierarchy”

After the dashboard is loaded, inject a lightweight fetch logger in DevTools / browser console:

```js
window.__fetchLog = [];
const orig = window.fetch;
window.fetch = (...args) => {
  const url = String(args[0]);
  window.__fetchLog.push(url);
  return orig(...args);
};
```

Then:

1. Start on 旅線分析.
2. Switch to 意見搜尋.
3. Switch back to 旅線分析.
4. Check:

```js
window.__fetchLog.filter(u => u.includes('/satisfaction/hierarchy')).length
```

Expected: `0` for tab-switch-only return when frontend cache/state is valid.

If the page has a manual refresh button, click it and verify the count increases to `1`; this proves the no-refetch behavior did not remove the explicit refresh path.

## Frontend checks for opinion search date fields

- The search area should show 起日 / 迄日 date inputs.
- Default range should be local current month first day through today.
- API requests should include `start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`.
- Result cards should show `日期 YYYY-MM-DD`.
- The summary line should show the active date range.

## Local UI behavior regression pattern

For small dashboard card behavior changes, prefer extracting pure display/sort helpers and testing them directly when there is no full frontend test harness yet. Example pattern from the route-card score display fix:

- Extract helper logic from `Analytics.tsx` into a small TypeScript module (for example `src/components/dashboard/analyticsMetrics.ts`).
- Add a minimal Node assertion script under `frontend/scripts/` that compiles only the helper with `tsc --outDir .tmp-* --module NodeNext --moduleResolution NodeNext ...` and runs the generated JS.
- Add an npm script that removes the temp build dir before and after the test.
- Verify the specific behavior first, then run `npm run build` and `npm run lint`.

When implementing route-card missing-score behavior, treat `0`, `null`, `undefined`, and non-finite metric values as missing display values: render `-`, use neutral gray classes for the metric cell/number, and rank missing passenger scores as `Number.POSITIVE_INFINITY` so those cards sort last while preserving stable original order among missing cards.

## Path and process pitfalls

- If the workspace path contains shell metacharacters such as `[P1]`, Hermes `terminal(workdir=...)` may reject that directory. Use a safe workdir such as `/Users/wujinan/Documents` and start commands with a quoted `cd '<actual workspace path>' && ...` instead of changing the tool workdir directly.
- The parent `[P1] ...` workspace may not be a git repo even though `multi-agent-service` and `multi-agent-web` are separate git repos. Check branch/status inside each subrepo before edits or server work; do not treat parent-level `fatal: not a git repository` as proof the project is unmanaged.
- Background `uvicorn`/`npm run dev` wrapper PIDs may differ from the actual listening PIDs reported by `lsof`; report both the Hermes process IDs and the listening-port verification when handing the URL to the user.

## Known benign log noise

- `passlib` may print `(trapped) error reading bcrypt version` / `module 'bcrypt' has no attribute '__about__'` and still return `POST /api/v1/auth/login 200 OK`. Treat it as a warning unless login actually fails.
- An initial `OPTIONS /api/v1/auth/login 400` can happen if the frontend is opened from `127.0.0.1:5173`; reopen via `localhost:5173` before diagnosing auth.
