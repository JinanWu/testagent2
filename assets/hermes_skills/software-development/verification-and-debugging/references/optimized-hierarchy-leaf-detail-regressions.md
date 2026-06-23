# Optimized hierarchy → leaf-detail display regressions

Use this when a dashboard loads a fast/initial hierarchy for performance, then a leaf-level page (tour, member, record detail, etc.) is missing heavy text fields that used to appear.

## Durable pattern

1. Trace whether the initial hierarchy intentionally omits heavy fields (full text, history, large arrays) for performance.
2. Do not re-expand the initial hierarchy payload unless the UI truly needs every field upfront.
3. Prefer a leaf-detail lazy load when drilling into the final node:
   - Add/confirm a detail API for the leaf record.
   - Cache detail responses by leaf id in the frontend.
   - Render a small loading state and fallback to the fast hierarchy object until detail returns.
   - Replace only the current leaf object with the full detail object; preserve the rest of the hierarchy state.
4. If the leaf id is a hierarchical path containing `/`, FastAPI needs a path converter such as `{leaf_id:path}`. Frontend callers should use `encodeURIComponent(leafId)` so spaces, slashes, and non-ASCII path segments are safe in the URL.
5. Add a regression test for the route, not just the data manager:
   - monkeypatch the service/data lookup to capture the incoming id,
   - call the real FastAPI route with a slash-containing id,
   - assert the captured id matches and the missing display field is present in the JSON.

## Verification checklist

- Backend route accepts slash-containing ids and returns the full detail field.
- Frontend build/typecheck passes after adding the lazy-load state.
- UI has a loading/error state for the detail fetch.
- Existing optimized initial-load behavior remains unchanged.
