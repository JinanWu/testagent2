# Dashboard tab cache + opinion date filter pattern

Use when a React/Vite dashboard has two adjacent tabs/pages (for example 旅線分析 and 意見搜尋), and the requirements are:
- switching away and back must not re-call an expensive hierarchy/API endpoint when query conditions are unchanged;
- data should refresh only after a TTL, condition changes, or a manual refresh;
- an opinion/search page needs a date column plus date-range query defaults;
- BigQuery or the real database is temporarily unavailable.

## Root-cause checklist

1. Confirm whether route/tab switching unmounts the page component.
   - If it does, component-local `useState` is not enough; the cache must live above the page instance (API module singleton, context/provider, or query cache library).
2. Put the cache at the API client boundary when the expensive endpoint has no page-local query conditions.
   - Keep `{ data, fetchedAt, inFlight }`.
   - TTL example: `4 * 60 * 60 * 1000`.
   - Return cached data when fresh.
   - Return the same `inFlight` promise for concurrent callers.
   - Expose `forceReload` for manual refresh.
3. Add a visible manual refresh affordance if the requirement says manual refresh should bypass cache.
4. For search date filters, treat backend and frontend as one contract:
   - API query params: `start_date`, `end_date` in `YYYY-MM-DD`.
   - Default frontend range: local current month first day through today.
   - Default backend range: database-side current month first day through today, ideally in the business timezone.
   - Response item includes normalized `date` in `YYYY-MM-DD`.
5. Without live BigQuery/database access, test SQL-shaping and endpoint parameter plumbing instead of executing real queries.
   - Unit-test the WHERE clause includes the default range.
   - Unit-test explicit start/end dates appear in SQL.
   - Unit-test SELECT exposes a normalized `date` field.
   - Unit-test FastAPI/server layer passes date params to the data manager/loader.
6. If doing browser verification offline, use a small local mock API that counts endpoint hits:
   - load the analysis page once;
   - switch to opinions;
   - switch back;
   - assert the hierarchy endpoint count stayed at 1 unless refresh/TTL forced reload.

## BigQuery date expression pattern

When source dates are heterogeneous, centralize the date expression so SELECT and WHERE agree:

```python
def _opinion_date_expr() -> str:
    return (
        "COALESCE("
        "SAFE_CAST(create_time AS DATE), "
        "DATE(SAFE_CAST(create_time AS DATETIME)), "
        "DATE(SAFE_CAST(create_time AS TIMESTAMP)), "
        "SAFE.PARSE_DATE('%Y%m%d', CAST(tour_date AS STRING)), "
        "SAFE_CAST(tour_date AS DATE)"
        ")"
    )
```

Then use it in both places:

```sql
FORMAT_DATE('%Y-%m-%d', <expr>) AS date
WHERE <expr> BETWEEN DATE_TRUNC(CURRENT_DATE('Asia/Taipei'), MONTH)
                 AND CURRENT_DATE('Asia/Taipei')
```

## Frontend cache shape

```ts
const HIERARCHY_CACHE_TTL_MS = 4 * 60 * 60 * 1000
const hierarchyCache = { data: null, fetchedAt: 0, inFlight: null as Promise<HierarchyData> | null }

async function getHierarchy(options: { forceReload?: boolean } = {}) {
  const now = Date.now()
  const isFresh = hierarchyCache.data && now - hierarchyCache.fetchedAt < HIERARCHY_CACHE_TTL_MS
  if (!options.forceReload && isFresh) return hierarchyCache.data
  if (!options.forceReload && hierarchyCache.inFlight) return hierarchyCache.inFlight
  hierarchyCache.inFlight = fetch(url)
    .then(/* parse */)
    .then(data => {
      hierarchyCache.data = data
      hierarchyCache.fetchedAt = Date.now()
      return data
    })
    .finally(() => { hierarchyCache.inFlight = null })
  return hierarchyCache.inFlight
}
```

## Verification commands used successfully

- Backend focused tests: `python3 -m pytest tests/test_dashboard_opinions.py -q`
- Frontend type/build: `npm run build`
- Frontend lint: `npm run lint` (warnings may be pre-existing; distinguish errors from existing warnings).
