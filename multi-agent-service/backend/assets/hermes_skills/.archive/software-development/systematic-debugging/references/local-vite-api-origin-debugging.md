# Local Vite API origin debugging

Use this when a Vite/React frontend is running locally but login/API calls fail with vague browser errors such as `Load failed`, even though the local backend health check is green.

## Pattern observed

A local Vite dev server can still compile `VITE_*` values from `.env.local` or `.env.development`. If `VITE_API_BASE_URL` points at a deployed Cloud Run/service URL, the browser will bypass the local Vite proxy and send login/API calls to the remote service. Local curl tests against `/api/...` may pass while the browser still fails, because the browser bundle is using the absolute remote API origin.

## Debugging checklist

1. Verify the backend is actually listening and healthy:
   - `curl http://127.0.0.1:8000/health`
   - `lsof -nP -iTCP:8000 -sTCP:LISTEN`
2. Verify the API through the Vite proxy:
   - `curl http://localhost:5173/api/v1/...`
3. Inspect the compiled Vite module, not just source code:
   - `curl http://localhost:5173/src/api/config.ts | sed -n '1,5p'`
   - Check whether `import.meta.env` includes `VITE_API_BASE_URL` with a remote origin.
4. If the browser is hitting a remote API, restart Vite with the variable cleared or set to local:
   - `VITE_API_BASE_URL= npm run dev -- --host 127.0.0.1`
   - or update the local env file intentionally for local development.
5. Re-verify the compiled module now shows `VITE_API_BASE_URL: ""`, then hard-refresh the browser.

## Pitfalls

- `env -u VITE_API_BASE_URL npm run dev` does not override values loaded from `.env.local`; Vite still reads those files. Set `VITE_API_BASE_URL=` explicitly if you need to force an empty value for the process.
- Do not conclude the backend login endpoint is broken until you compare the browser bundle's compiled API origin with the API path you tested via curl.
- If `APP_API_BASE_URL` is relative, Vite proxy can route `/api` to the local backend. If it is absolute, the proxy is bypassed.
