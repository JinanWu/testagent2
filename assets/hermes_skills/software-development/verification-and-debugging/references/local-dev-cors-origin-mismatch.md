# Local dev CORS origin mismatch

Use this when a browser login or API call shows `Load failed`, `Failed to fetch`, or a CORS preflight error during local full-stack development.

## Symptom pattern
- Frontend loads normally.
- Network preflight to the backend returns `400 Disallowed CORS origin` or the browser reports a generic fetch failure.
- Direct `curl` to the backend works, but the browser path fails.

## What to check first
1. The exact frontend origin in the browser URL bar.
   - `http://localhost:<port>` and `http://127.0.0.1:<port>` are different origins.
2. The backend CORS allow list or debug-mode defaults.
3. The Vite dev server URL and any proxy/base URL variables.

## Quick verification
- Probe the preflight with the browser origin:
  - `OPTIONS /api/...` or the login endpoint with `Origin: http://localhost:5173`
  - Repeat with `Origin: http://127.0.0.1:5173` if the browser uses that host.
- If one origin is accepted and the other is rejected, the problem is origin mismatch, not auth.

## Fix pattern
- Keep the browser URL and the backend CORS allow list on the same host form.
- If the project expects `localhost` in debug mode, open the app at `http://localhost:<port>` instead of `http://127.0.0.1:<port>`.
- If both host forms should work, add both origins to the backend dev allow list.

## Reporting
Include:
- frontend URL used
- backend port
- preflight status code
- accepted/rejected origins
- whether direct backend curl succeeded
