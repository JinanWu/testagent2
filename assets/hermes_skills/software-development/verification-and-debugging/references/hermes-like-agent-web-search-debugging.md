# Hermes-like agent web_search debugging

Use when a FastAPI/SSE Hermes-like wrapper exposes Hermes `web_search` and users report that web search is weird, empty, noisy, or inconsistent.

## Failure patterns observed

1. Backend fallback surprises
- Hermes reads `web.backend`, `web.search_backend`, and `web.extract_backend` from `~/.hermes/config.yaml`.
- If these are blank and no paid/search provider env vars are configured, Hermes may auto-detect the free `ddgs` backend.
- `ddgs` can work, but result quality is less stable than Tavily/Exa/Parallel/SearXNG/Firecrawl: Chinese queries may return Simplified Chinese aggregators, stale pages, or broad/non-official results.
- Do not diagnose this as a runtime/tool-loop failure unless the tool call itself failed; report the active backend separately from answer quality.

2. Argument-shape mismatch
- Hermes `web_search` expects `query` and `limit`.
- Model calls may emit aliases such as `q`, `search`, `keyword`, `keywords`, `max_results`, `num_results`, or `count`.
- If `q` is passed through unnormalized, Hermes/DDGS can return `DuckDuckGo search failed: query is mandatory`.

3. Tool result too noisy
- Some providers return very long `description` fields, sometimes entire article/Wikipedia-like paragraphs.
- Feeding this raw result back to the model makes final answers look strange even when the search call succeeded.

## Recommended wrapper behavior

Before dispatching to Hermes `handle_function_call('web_search', ...)`:

```python
query = args.get('query') or args.get('q') or args.get('search') or args.get('keyword') or args.get('keywords')
limit = args.get('limit') or args.get('max_results') or args.get('num_results') or args.get('count') or 5
clean_args = {'query': str(query).strip(), 'limit': max(1, min(int(limit), 10))}
```

After the Hermes call:
- Parse JSON if possible.
- Keep only `title`, `url`, `description`, `position` for each result.
- Trim descriptions to a few hundred characters.
- Add a diagnostic block such as:

```json
"_aiagent_diagnostics": {
  "search_backend": "ddgs",
  "note": "normalized aliases and trimmed long descriptions"
}
```

## Verification recipe

1. Direct wrapper probe:
```python
工具.執行('web_search', {'q': 'OpenAI 最新消息', 'max_results': 2})
```
Expected:
- no `query is mandatory`
- result has 1-2 `data.web` rows
- `_aiagent_diagnostics.search_backend` is present
- descriptions are bounded in length

2. API probe if exposed:
```text
GET /api/web-search-debug?query=OpenAI 最新消息&limit=2
```
Expected:
- `backend` is visible
- `normalized_args.query` equals the requested query
- result success/failure is explicit

3. End-to-end SSE probe:
```text
GET /api/chat/stream?message=幫我網路搜尋 OpenAI 最新消息，列出兩個結果並附網址&role=admin
```
Expected:
- `tool.started` for `web_search`
- tool arguments include `query` and `limit`
- final answer includes URLs

## Reporting guidance

Separate these in the final report:
- Runtime/tool-loop health: did the model call `web_search`, did the tool return success, did SSE continue to final answer?
- Backend/provider quality: which backend is active and whether it is likely to return lower-quality results.
- Fixes applied: argument normalization, result cleanup, diagnostics endpoint, regression tests.

If quality remains poor and backend is `ddgs`, recommend configuring a stronger backend rather than endlessly tuning the wrapper.