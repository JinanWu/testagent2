# Local Vite + FastAPI login debugging

Use this when a local dashboard/front-end app starts successfully but the user cannot log in.

## Pattern observed

A Vite React frontend may show a valid login page and default credentials, but login still fails because the backend API proxy target is not running.

Concrete shape:
- Frontend dev server: `http://127.0.0.1:5173/`
- Vite proxy maps `/api` and `/dashboard/api` to `http://localhost:8000`
- Login form posts to `/api/v1/auth/login`
- Backend should expose `/health` and `/api/v1/auth/login` on port 8000

## Investigation sequence

1. Read frontend auth files first:
   - `src/pages/LoginPage.tsx` for displayed/default credentials
   - `src/contexts/AuthContext.tsx` for login flow
   - `src/api/config.ts` and `vite.config.ts` for API base URL/proxy target
2. Check whether the backend proxy target is listening:
   - `lsof -nP -iTCP:8000 -sTCP:LISTEN || true`
   - `curl -sS --max-time 5 http://127.0.0.1:8000/health || true`
3. If 8000 is not listening, start the backend before blaming credentials.
4. Verify health before telling the user to retry login.

## Passenger-survey / multi-agent local dev recipe

For `passenger-survey-data-fix-*` workspaces with:
- `multi-agent-web/frontend`
- `multi-agent-service/backend`

Start frontend:

```bash
cd multi-agent-web/frontend
npm run dev -- --host 127.0.0.1
```

Start backend from the backend directory, with the parent service directory on `PYTHONPATH` so `dashboard_backend` can be imported:

```bash
cd multi-agent-service/backend
PYTHONPATH=.. python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then verify:

```bash
curl -sS --max-time 5 http://127.0.0.1:8000/health
curl -I --max-time 5 http://127.0.0.1:5173/
```

## Pitfall

If the user asks to launch a local project and then reports they cannot log in, do not stop at explaining that the backend is missing when the project has a documented local-dev command. Start the missing backend service and verify `/health`, unless a safety/tool guard explicitly prevents the exact action.