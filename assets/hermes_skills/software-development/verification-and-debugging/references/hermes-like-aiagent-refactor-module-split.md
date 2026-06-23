# Hermes-like aiagent module-split refactor notes

Use this when refactoring a monolithic FastAPI + AgentRuntime file into modules while preserving API/DB/function behavior.

## Safe sequence

1. Branch from `main` for the refactor; do not build on a bugfix/feature branch unless explicitly requested.
2. Establish the current test baseline first. In the aiagent session that produced this note, the target was `35 passed` on `main`, not the older planning value of `29 passed`.
3. Move code in behavior-preserving chunks: settings, dataclasses/Pydantic models, skills, tool registry/default tools, Hermes bridge, workspace scanner, model clients, SQLite store, runtime, gateway, API route modules.
4. Keep `後端/服務.py` as the composition root and re-export compatibility names used by tests/importers (`應用`, `設定`, `儲存`, `對話訊息`, `智慧代理執行器`, etc.).
5. After each chunk, run `py_compile` and targeted tests before continuing.

## FastAPI route migration pitfalls

When slicing a monolithic file by line ranges, decorators can be accidentally left behind. This produces routes that silently disappear while the endpoint functions still exist.

Checklist after moving routes:

- Print or inspect `應用.routes` and compare against expected routes.
- Verify first endpoint in every route module; this is where decorator-loss is most likely when the copied block starts at `def` rather than `@router.get/post`.
- Include root frontend route `/`; static `/static/index.html` can still work while `/` returns `{"detail":"Not Found"}`.
- Add a regression test such as `test_首頁回傳前端HTML` for `/` returning HTML.

Examples of decorators that were easy to miss during aiagent split:

```python
@router.get('/')
def 首頁() -> FileResponse: ...

@router.get('/api/session-search')
def 搜尋對話工作階段(...): ...

@router.get('/api/tools')
def 工具列表(...): ...

@router.get('/api/approvals')
def 批准請求列表(...): ...
```

## Import migration pitfalls

Moving code out of a monolith often loses module-level imports/constants that nested functions depend on:

- Dataclass decorators: `@dataclass`, `@dataclass(frozen=True)`.
- Safe calculate constants: `允許運算 = {ast.Add: operator.add, ...}`.
- API helpers: `uuid`, `json`, `FileResponse`, `StreamingResponse`, `Query`, `Header`, `Body`.
- Storage helpers: `hashlib`, `re`, `sqlite3`, `Path`, `datetime/timezone`.
- Feature helpers: `掃描工作區上下文`, `讀取網路搜尋後端`.

Use failures as signals, but prefer a route table / AST scan so you do not rely only on pytest coverage.

## Verification gates used

```bash
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile 後端/*.py 後端/*/*.py tests/*.py
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

Run a small AST/docstring/naming check for Traditional-Chinese Python codebases:

- no missing docstrings on classes/functions;
- no ASCII-only non-test class/function names except dunder/framework-required names.

Then start a live smoke server and verify:

```text
GET /            -> 200 text/html
GET /api/health  -> 200 application/json
GET /api/tools   -> tools list
POST /api/chat   -> completed response in fake mode
```

## Reporting pattern

Report before/after line counts for the composition root and largest extracted modules, e.g. `後端/服務.py` reduced from ~2755 lines to ~59 lines, and list the new module tree. Be explicit that API contract and DB schema were intentionally unchanged.
