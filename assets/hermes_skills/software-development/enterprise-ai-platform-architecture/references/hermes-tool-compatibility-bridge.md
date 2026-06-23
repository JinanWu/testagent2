# Hermes Tool Compatibility Bridge Pattern

Use this reference when building an internal Hermes-like web Agent platform that should reuse Hermes Agent's real tool handlers rather than only copying tool names or schemas.

## Problem signal

A common incomplete implementation is to copy Hermes `skills/` and a tool catalog, then expose only a few local MVP tools. This makes the project look Hermes-like, but prompts such as "go search the web" fail because `web_search` is only a catalog entry, not an executable handler.

## Durable pattern

For a prototype that lives next to a Hermes checkout, add a compatibility bridge:

1. Configure the Hermes source path, for example:
   - `AIAGENT_HERMES_SOURCE_PATH=/Users/wujinan/Documents/hermes-agent`
   - `AIAGENT_ENABLE_HERMES_TOOLS=true`
   - `AIAGENT_HERMES_TOOLSETS=hermes-api-server`
2. Insert the Hermes source path into `sys.path`.
3. Import Hermes `model_tools`:
   - `get_tool_definitions(enabled_toolsets=[...], quiet_mode=True)`
   - `handle_function_call(function_name, function_args, task_id=..., user_task=...)`
4. For every returned function schema that passed Hermes `check_fn`, register an internal tool whose handler delegates to `handle_function_call`.
5. Expose only the registered/executable tool names to the model and to policy. Do not expose catalog-only tools as callable.
6. Return bridge status in `/api/health`: selected toolsets, registered count, skipped tools, and load errors.

## Recommended default toolset

For a web/API platform, default to `hermes-api-server`, not `hermes-cli`:

- `hermes-api-server` includes tools suitable for HTTP runtime use: web, file, terminal/process, browser basics, skills, memory, session_search, execute_code, delegate_task, cronjob, etc.
- `hermes-cli` may include interactive tools such as `clarify` or `text_to_speech` that are less appropriate for a web request/response API unless corresponding UI handling exists.

Allow override with a comma-separated env var such as `AIAGENT_HERMES_TOOLSETS=hermes-api-server,web`.

## Web search setup note

Hermes `web_search` only registers when its backend check passes. If no paid backend credentials are configured, install and enable a free backend such as `ddgs`:

```bash
python3 -m pip install ddgs
```

Then `get_tool_definitions(enabled_toolsets=['web'], quiet_mode=True)` should include `web_search` and `web_extract` when Hermes detects ddgs.

## Verification checklist

Run deterministic tests in fake-model mode and live smoke tests against the running API:

```bash
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q

python3 - <<'PY'
import json, urllib.request
base='http://127.0.0.1:8000'
h=json.loads(urllib.request.urlopen(base+'/api/health').read().decode())
print(h['tools_count'], len(h['hermes_tools']['registered']), h['hermes_tools']['error'])
assert 'web_search' in h['hermes_tools']['registered']
assert 'terminal' in h['hermes_tools']['registered']
PY
```

Live tool-loop smoke prompts:

- "請使用 web_search 查詢 Vertex AI Gemini 2.5 Flash Lite，回覆一個搜尋結果標題和網址"
  - Expected RunEvents include `tool.started` with `tool='web_search'`.
- "請使用 terminal 工具執行 `printf aiagent-terminal-ok`，只回覆輸出內容"
  - Expected RunEvents include `tool.started` with `tool='terminal'` and final answer contains `aiagent-terminal-ok`.

## Pitfalls

- Do not claim tools are "built in" if only schemas/catalog entries exist. Separate `catalog_tools` from `executable_tools` in health, UI, and prompts.
- Gemini responses can duplicate text if the adapter reads both `resp.text` and iterates candidate parts. Prefer one extraction path; when manually parsing function calls, build text from parts only.
- Some Hermes tools remain absent by design until credentials/dependencies pass Hermes `check_fn` (Home Assistant token, Spotify auth, platform tokens, browser backend, etc.). Represent this as availability, not as a permanent platform limitation.
- High-risk tools such as terminal/write_file/patch need policy, audit, and ideally approval or sandbox boundaries before broad enterprise rollout, even if the bridge can execute them.
