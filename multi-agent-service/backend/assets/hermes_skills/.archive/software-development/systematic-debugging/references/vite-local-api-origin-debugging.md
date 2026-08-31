# Vite local API origin debugging

Use this when a Vite/React frontend is running locally but login or API calls show browser errors such as `Load failed`, while direct `curl` to the local backend succeeds.

## Symptom pattern

- Backend health check on `localhost:8000` succeeds.
- Direct backend login/API request succeeds.
- Vite proxy request such as `localhost:5173/api/v1/...` succeeds from curl.
- Browser login still fails with `Load failed`.

## Common root cause

The frontend is not using the relative `/api` path and therefore bypasses the Vite proxy. A Vite env file or process env may inject an absolute origin, for example:

```text
VITE_API_BASE_URL=https://example-cloud-run-url
```

If the app builds API URLs from `import.meta.env.VITE_API_BASE_URL`, browser requests go to the remote origin instead of local `localhost:5173/api/...`. This can fail due to CORS, remote auth state, remote service health, or network policy even though local backend is fine.

## Evidence to gather before fixing

1. Confirm backend is listening and healthy:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN || true
curl -sS --max-time 5 http://127.0.0.1:8000/health
```

2. Confirm Vite proxy works from the frontend origin:

```bash
curl -i --max-time 10 -X POST http://localhost:5173/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
```

3. Inspect the transformed Vite module actually served to the browser:

```bash
curl -sS --max-time 5 http://localhost:5173/src/api/config.ts | sed -n '1,8p'
```

Look for `import.meta.env` containing `VITE_API_BASE_URL`. This confirms what the browser sees; reading only source files can miss env injection.

4. Check env files/process env without dumping secrets. It is enough to identify whether `.env.local`, `.env.development`, or the process env defines `VITE_API_BASE_URL`.

## Local fix/workaround

For local proxy-based development, restart the Vite server with the variable explicitly blank so API URLs remain relative:

```bash
VITE_API_BASE_URL= npm run dev -- --host 127.0.0.1
```

Then re-check:

```bash
curl -sS --max-time 5 http://localhost:5173/src/api/config.ts | sed -n '1,4p'
```

Expected shape:

```text
"VITE_API_BASE_URL": ""
```

Finally verify the browser-origin API call again through port 5173.

## Pitfalls

- `env -u VITE_API_BASE_URL npm run dev` may not be enough if `.env.local` or `.env.development` defines the variable; Vite loads env files after process env setup. Explicitly setting `VITE_API_BASE_URL=` for the process overrides the file value for this local run.
- Do not conclude the backend is broken just because the browser says `Load failed`; first compare direct backend, Vite proxy, and the transformed Vite config served to the browser.
- If a browser tab was opened before the restart, hard-refresh or reopen `/login` after changing the env.