# Dashboard opinion infinite-scroll verification with mock API

Use this when the 心情指數儀表板 opinion-search page must be checked locally but live BigQuery / gcloud access is unavailable.

## What to verify

The opinion page should:
- request page 1 with the current date/label/keyword filters and `limit=20`;
- append page 2/3 results when the bottom sentinel enters the viewport;
- preserve the original rows rather than replacing them on non-reset loads;
- stop when rendered item count reaches `total` and show the "已顯示全部" message.

## Mock-backend pattern

Run a tiny local HTTP server on the same port normally used by the FastAPI backend (`8000`) so Vite proxy still exercises the real browser code path:

- `GET /dashboard/api/v1/opinions/label-definitions` returns a small label/group payload.
- `GET /dashboard/api/v1/opinions?page=N&limit=20...` returns deterministic slices from a fixed list, e.g. 45 rows so pages are 20 + 20 + 5.
- `POST /api/v1/auth/login` and `GET /api/v1/auth/me` can return simple mock auth payloads if the app requires login before dashboard access.

Keep this mock temporary; shut it down after verification so future local runs do not accidentally demo fake data.

## Browser evidence to collect

In the browser console, inspect resource requests and DOM state:

```js
performance.getEntriesByType('resource')
  .filter(e => e.name.includes('/dashboard/api/v1/opinions?'))
  .map(e => e.name)

[...document.querySelectorAll('article')]
  .map(a => a.innerText.match(/意見 #(\d+)/)?.[1])
  .filter(Boolean)
```

Expected sequence for a 45-row mock:
- after initial load: `page=1&limit=20`, 20 articles;
- after scrolling near bottom once: `page=2&limit=20`, 40 articles;
- after scrolling to bottom again: `page=3&limit=20`, 45 articles and the all-loaded footer.

## Pitfalls

- In React development with `React.StrictMode`, the initial reset effect may issue `page=1` twice. If reset uses `setItems(rows)`, this usually does not duplicate rendered rows, but it can create extra API/BigQuery cost and a possible race if responses arrive out of order.
- Do not confuse an initial duplicate `page=1` request with a broken infinite-scroll append path. Verify page 2/3 requests and article count before calling the scroll loading broken.
- If the frontend is configured with an absolute `VITE_API_BASE_URL`, Vite proxy is bypassed and the mock server will not receive requests. Confirm the compiled `/src/api/config.ts` shows `VITE_API_BASE_URL: ""` for local proxy testing.
