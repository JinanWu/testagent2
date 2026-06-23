# Hermes-like 專案：Git 初始化與繁中 Python 可讀性規則

Use this reference when implementing a Hermes-like Agent project for this user, especially `/Users/wujinan/Documents/aiagent`-style prototypes.

## User preference captured

The user explicitly wants Python code in Traditional Chinese for readability experiments. This includes class names, function names, variables, comments, and docstrings where the code is project-owned.

Do not blindly translate external contracts or library APIs. Keep English where changing it would break interoperability.

## Chinese coding style rule

Prefer readable Traditional Chinese names with verb + object shapes for functions:

- `建立預設工具註冊表`
- `註冊Hermes相容工具`
- `標準化網路搜尋參數`
- `整理網路搜尋結果`
- `掃描工作區上下文`
- `讀取對話工作階段`
- `更新對話工作階段標題`
- `新增執行紀錄`
- `完成執行紀錄`

For classes, use noun phrases:

- `智慧代理執行器`
- `智慧代理閘道`
- `SQLite儲存庫`
- `聊天請求`
- `背景程序請求`

For variables, prefer descriptive Chinese nouns:

- `資料庫`, `資料列`, `工具名稱`, `整理後參數`, `原始結果`, `內容列表`, `生成設定`, `結束原因`, `阻擋原因`

## Preserve external API names

Keep these in English unless explicitly redesigning the contract:

- HTTP routes: `/api/chat`, `/v1/chat/completions`
- JSON fields: `message`, `session_id`, `feature_id`, `skill_id`, `events`, `status`, `answer`, `error`
- SDK parameters and attributes: `model`, `contents`, `config`, `tools`, `text`, `function_declarations`
- OpenAI-compatible response fields: `choices`, `delta`, `finish_reason`, `usage`
- Hermes tool schema fields: `name`, `description`, `parameters`, `required`, `properties`

Pitfall: mechanical replace can corrupt external APIs (`datetime.now`, `operator.sub`, `re.sub`, `google.genai`, `text/event-stream`, Gemini `generate_content(contents=..., config=...)`). After any broad rename, run `py_compile` and the full deterministic tests.

## Verification after localization refactors

Run:

```bash
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile 後端/服務.py tests/test_runtime.py tests/test_api.py
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

Also run an AST check for custom definitions:

- no missing docstrings, except `__init__` if intentionally omitted
- no project-owned pure-English class/function names outside pytest's required `test_` prefix

## Git initialization checklist for generated Agent projects

When the project is not already a git repo and the user asks to commit/push:

1. Verify repo state first:
   - `git rev-parse --show-toplevel`
   - `git status --short --branch`
   - `git remote -v`
2. Add `.gitignore` before `git add .`.
3. Ignore runtime and local artifacts:
   - `.env`, `.env.*`, except `.env.example`
   - `資料/*.sqlite3`, `*.sqlite3`, `*.db`
   - `__pycache__/`, `.pytest_cache/`, `.DS_Store`
   - copied Hermes skill maintenance metadata: `內建資料/skills/.archive/`, `.curator_backups/`, `.hub/`, `.usage.json*`, `.curator_state`, `.bundled_manifest`
4. Add a non-secret `.env.example` with only safe defaults and placeholders.
5. Search staged content for secret-like patterns before commit. Treat matches in documentation examples separately from real credentials.
6. Run deterministic tests before commit.
7. Commit with a conventional message, then push and verify `origin/main` or the requested branch with `git ls-remote` and `git log --oneline -1 --decorate`.

## Why this matters

This project class often copies large Hermes skills/tool manifests. Without a `.gitignore`, local SQLite state, pycache, curator backups, archived skills, and lock files can bloat the first commit or leak operational state. The first commit should include source, docs, tests, safe config examples, built-in skill snapshots, and tool manifests — not runtime data.