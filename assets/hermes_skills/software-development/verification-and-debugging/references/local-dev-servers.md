# Local full-stack dev server notes

Use this when starting a frontend/backend pair for browser QA in a multi-worktree repo.

## Checks before launch
- Read the repo's local-dev config first: Vite config, backend entrypoint, proxy/base URL settings.
- Check whether the documented ports are already in use and identify the PID/worktree owning them.
- If the target path contains shell metacharacters (for example `[` or `]`), use a simple safe `workdir` and `cd` into the repo inside the command.

## Launch pattern
- Start the backend from the directory that makes its imports resolve naturally.
- If the backend imports a sibling package from the repo root, set `PYTHONPATH` to include the backend/root path instead of editing code.
- When the documented ports are occupied by another user's/session's process, do not kill them unless the user explicitly asks. Pick nearby available alternatives (for example frontend `5189` instead of occupied `5188`, backend `8080` instead of occupied `8000`) and report both the avoided and chosen ports.
- For Vite apps that can bypass the proxy with `VITE_API_BASE_URL`, prefer launching the frontend with `VITE_API_BASE_URL=http://127.0.0.1:<backend-port>` when using an alternate backend port. If the backend has restrictive CORS and the frontend calls it directly, set `CORS_ALLOW_ORIGINS` (or the repo's equivalent) to include the chosen frontend origin for the local run.
- Start the frontend with the backend origin injected explicitly when the app reads `VITE_API_BASE_URL`.

## Verification
- Verify backend health/API directly.
- Verify the frontend HTML route.
- Verify at least one frontend-proxied API path through the frontend port.

## Report
Include:
- URLs and ports
- PID/session IDs
- Any resolved port conflict
- The exact launch command shape if imports required `PYTHONPATH` or an explicit `cd`
