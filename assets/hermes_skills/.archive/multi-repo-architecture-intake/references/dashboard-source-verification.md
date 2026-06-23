# Dashboard source verification and fallback behavior

This note captures the verification pattern observed for the `multi-agent-web` dashboard surface.

## What the frontend does
- Route `/dashboard` renders `DashboardLayout` with nested pages:
  - `/dashboard` → `AnalyticsPage`
  - `/dashboard/opinions` → `OpinionsPage`
  - `/dashboard/tracking` → `TrackingPage`
- `AnalyticsPage` calls `dashboardApi.getHierarchy()` on mount.
- If that call fails, the page does not go blank; it falls back to `FALLBACK_HIERARCHY`.
- The fallback explicitly includes demo text saying the formal hierarchy data could not be loaded.

## API wiring
- `frontend/src/api/config.ts` normalizes `VITE_API_BASE_URL` and builds:
  - `APP_API_BASE_URL = /api/v1`
  - `DASHBOARD_API_BASE_URL = /dashboard/api/v1`
- The frontend dashboard fetches:
  - `GET {DASHBOARD_API_BASE_URL}/satisfaction/hierarchy`
  - `GET {DASHBOARD_API_BASE_URL}/opinions`
  - `GET {DASHBOARD_API_BASE_URL}/opinions/label-definitions`
- Vite dev proxy forwards `/api` and `/dashboard/api` to `localhost:8000`.

## Backend source of truth
- `multi-agent-service/backend/app/main.py` mounts `dashboard_backend.server` at `/dashboard`.
- `dashboard_backend/data_loader.py` reads from BigQuery defaults:
  - project: `dev-cola-rd`
  - dataset: `passenger_survey_pred_dashboard`
  - tree table: `opinion_tree_metrics_summary_snapshot`
  - opinion table: `project_semantic_features`

## Why the UI can look empty even when data exists
1. The dashboard API request fails, so the frontend uses fallback/demo data.
2. The reverse proxy or deployed origin points `/dashboard/api` somewhere else.
3. The backend environment variables (`DASHBOARD_BQ_*`) point to a dataset/table with no live rows.
4. The user is looking at a page that is static by design (e.g. the tracking demo surface or other mock content).

## Verification checklist
- Confirm the browser route is on the intended page (`/dashboard` vs `/dashboard/opinions` vs `/dashboard/tracking`).
- Check the network/API response, not only the rendered UI.
- Confirm the dashboard backend is mounted in the service that is actually deployed.
- Confirm the dashboard backend reads the expected BigQuery project/dataset/table.
- If the UI shows demo text, treat that as a signal that the live hierarchy API failed.

## Practical pitfall
A live BigQuery snapshot alone is not enough to prove the dashboard is working. The frontend can silently mask backend/API failures by rendering fallback data.
