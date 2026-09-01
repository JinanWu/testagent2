# Dashboard local cache + date-filter verification

Use this when validating dashboard frontend changes that affect route/tab switching, API caching, and search date filters, especially for Vite + FastAPI dashboards backed by BigQuery.

## Pattern

1. Run the real backend locally with explicit project/dataset env vars, not a mock, when credentials are available:
   - `APP_ENV=development DEBUG=true GCP_PROJECT_ID=<project> DASHBOARD_BQ_PROJECT=<project> DASHBOARD_BQ_DATASET=<dataset> PYTHONPATH=<service-root>:<service-root>/backend python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. Run the Vite frontend pointed at that backend:
   - `VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173`
3. Verify backend API directly before browser work:
   - `/health`
   - `/dashboard/api/v1/satisfaction/hierarchy`
   - `/dashboard/api/v1/opinions/label-definitions`
   - a representative `/dashboard/api/v1/opinions?...&limit=3`
4. In the browser, wrap `window.fetch` before the interaction under test so SPA navigation calls are counted even when DevTools network state is not available:
   ```js
   window.__fetchLog = [];
   const orig = window.fetch;
   window.fetch = (...args) => {
     const url = String(args[0]);
     window.__fetchLog.push(url);
     return orig(...args);
   };
   ```
5. For a “switch away then back should not refetch” requirement:
   - Start on the page after the initial load has completed.
   - Install the fetch wrapper.
   - Switch to the other tab/page.
   - Switch back.
   - Check `window.__fetchLog.filter(u => u.includes('/satisfaction/hierarchy')).length` is `0`.
   - Trigger the explicit refresh control and confirm the count becomes `1`.
6. For date-filtered opinion search:
   - Confirm the UI shows start/end date controls near the search box.
   - Confirm default range is local month-start through local today.
   - If current-month data is absent, use the API source metadata from hierarchy (`tourDateStart` / `tourDateEnd`) to choose a populated range.
   - Verify the outgoing URL contains `start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`.
   - Verify API and UI rows display a normalized `date` field in `YYYY-MM-DD`.

## Pitfalls

- Do not validate against a mock after the user says real credentials are available; switch to the real backend and state the project/dataset used.
- If the dashboard is behind the app login, use `localhost` rather than `127.0.0.1` when the backend CORS development allowlist only includes `http://localhost:5173`.
- The initial hierarchy load may happen before the fetch wrapper is installed. For cache verification, install the wrapper after initial load and then verify the switch-away/switch-back delta, not total historical calls.
- If default current-month search returns 0 rows, that may be correct when the latest served dashboard snapshot is from a prior month. Confirm with the hierarchy `source` metadata before treating it as a bug.
