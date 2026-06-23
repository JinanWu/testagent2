---
name: hermes-like-agent-platform-development
description: "Build, extend, and verify Hermes-like enterprise AI agent platforms: FastAPI/Gateway/AgentRuntime, tool bridges, skills, sessions/runs, SSE, workspace context, process notifications, and Gemini ADC model integration."
---

# Hermes-like Agent Platform Development

Use this skill when building or extending an internal agent platform intended to behave like Hermes Agent, especially in `/Users/wujinan/Documents/aiagent` or similar projects.

## Core expectations

- Do not build a toy chat app when the ask is "make it like Hermes". Implement working platform surface area and verify it with real API calls/tests.
- Use 繁體中文 for Python identifiers, comments, and docstrings when the project convention requests it. Imports, HTTP field names, provider API names, and schemas can remain English.
- When converting Python to 繁中 coding style, prioritize readability over literal translation: class names should be clear nouns, functions should be 動詞 + 受詞, and variables should describe the domain object. Do not blindly replace English substrings inside external API names or SDK attributes.
- When Hermes is the reference implementation, inspect `/Users/wujinan/Documents/hermes-agent` or the Hermes docs before inventing behavior. For source-audit tasks, ground claims in the actual modules: `agent/system_prompt.py`, `agent/prompt_builder.py`, `agent/turn_context.py`, `agent/conversation_loop.py`, `hermes_state.py`, `model_tools.py`, and provider adapters such as `agent/gemini_native_adapter.py` / `agent/gemini_cloudcode_adapter.py`.
- Do not overstate parity. If the project uses gcloud ADC/Vertex AI Gemini, describe it as an enterprise provider-adapter substitution: Hermes's built-in Google AI Studio path uses `GOOGLE_API_KEY`/`GEMINI_API_KEY`, and its `google-gemini-cli` path uses OAuth PKCE/Cloud Code Assist rather than plain ADC.
- Keep the running service actually usable: after changes, run tests, syntax/docstring checks, restart the server, and hit health/API endpoints.
- For memory parity, do not make hard-coded deterministic regex auto-save the primary architecture. Hermes-style memory relies on strong memory tool schema, foreground model tool calls, and LLM-driven background review; use a generic “claimed to remember but no memory write occurred” guard rather than adding one-off fact parsers. Deterministic extraction should be optional fallback/test scaffolding, not the main design.
- For aiagent/Gemini ADC launches, prefer an environment override such as `AIAGENT_GCP_PROJECT=...` instead of editing source defaults when the code already reads the project from env. Confirm the effective project via `/api/health` before deeper testing.

## Architecture checklist

1. Gateway
   - Normalize headers/query/body into a user/session/feature request.
   - Enforce role/feature/tool policy before runtime execution.
   - Support session creation/resume and SSE/non-SSE chat entrypoints.

2. AgentRuntime
   - Maintain a real model/tool loop until the model reaches a final answer or max tool turns.
   - Persist user, assistant, tool messages, runs, run events, and tool calls.
   - Emit timeline events: `run.started`, `prompt.built`, `model.started`, `tool.started`, `tool.completed`, `message.completed`, failures, and platform-specific lifecycle events.

3. Tools and skills
   - Bridge Hermes tools with contextvars for `task_id`, `session_id`, `run_id`, `tool_call_id`, enabled tools, enabled toolsets, and current user task.
   - Provide local catalog APIs for tools and skills.
   - Keep skill text as reference context; do not execute arbitrary skill contents.

4. Sessions/runs
   - Provide APIs for listing, reading, renaming, deleting, and forking sessions.
   - Provide run status and run event APIs so frontends can reconnect after SSE interruption.
   - Preserve a stable session system prompt when prompt caching matters; append only current-turn additions like a selected skill or workspace context.

5. Workspace context
   - Support a `workdir` parameter.
   - Scan project guidance files such as `AGENTS.md`, `HERMES.md`, `CLAUDE.md`, and `.cursorrules` upward from workdir.
   - Inject the contents only inside an explicit untrusted/reference-only wrapper so file-level prompt injection cannot override system/developer/latest-user instructions.
   - Emit `workspace.context.loaded` or `workspace.context.error` events.

6. Context compression
   - For long sessions, keep head + protected tail and replace the middle with a reference-only compaction summary.
   - Clearly mark compacted content as background, not active user instruction.
   - Emit `context.compressed` and include context message counts in `prompt.built`.

7. Background processes
   - Use Hermes process registry patterns where available.
   - Drain process notifications (`watch_match`, `watch_disabled`, overflow, completion) into agent/run events so server readiness and background job completions are visible in the UI.
   - For servers, verify port availability, start with tracked background process, report PID/session_id/URL, and verify with curl/health/page requests.

8. Frontend
   - Provide a functional conversation page, not just a static mock.
   - Include session list/resume, role, skill selector, workdir input, events timeline, process panel, tool catalog, skill catalog, capabilities panel, and workspace context preview when these APIs exist.

9. Hermes source-parity audit
   - Treat terminal-only operation as an MVP CLI entrypoint/adapter, not as proof that Hermes's gateway is terminal-only.
   - Mirror Hermes prompt boundaries: `stable` identity/guidance/skills/environment, `context` user/context files, and `volatile` memory/user/session metadata.
   - Send tool schemas through the request/tool registry path; do not collapse them into plain system-prompt prose.
   - Keep OpenAI-compatible messages/tool_calls as the internal canonical shape where practical, then translate at provider adapter boundaries.
   - Describe SQLite persistence accurately: early-persist user turns for crash resilience; append assistant tool_calls/tool results to working messages; flush assistant/tool messages at persistence points.

## 繁中 Python naming checklist

When the user asks for Traditional Chinese coding style in a Python codebase:

1. Keep external contracts in English: HTTP routes, JSON fields, SDK keyword arguments, provider response attributes, tool schema keys, SQL table/column names if already public, and pytest's `test_` prefix.
2. Translate project-owned names only: classes, functions, internal helper names, local variables, comments, and docstrings.
3. Prefer readable domain names over short literal replacements:
   - good: `建立使用者上下文`, `讀取對話工作階段`, `整理網路搜尋結果`, `事件列表`, `原始結果`.
   - bad: mechanical replacements that create `datetime.目前時間`, `operator.子目錄`, `google.產生內容`, or `text/記錄事件-stream`.
4. After bulk renaming, run both syntax and behavior checks, then inspect for corrupted external APIs before reporting success:

```bash
AIAGENT_MODEL_MODE=fake python3 -m py_compile 後端/服務.py tests/test_runtime.py tests/test_api.py
AIAGENT_MODEL_MODE=fake python3 -m pytest -q
```

5. Run an AST audit for missing docstrings and accidental pure-English project-owned class/function names, but allow `__init__`, pytest `test_` prefixes, and external/public API fields.

## Verification checklist

Run all applicable checks before reporting success:

```bash
AIAGENT_MODEL_MODE=fake python3 -m pytest -q
AIAGENT_MODEL_MODE=fake python3 -m py_compile 後端/服務.py tests/test_runtime.py tests/test_api.py
```

Also run a docstring scan for Python functions/classes if the project convention requires docstrings, and verify the live server:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/v1/capabilities
curl 'http://127.0.0.1:8000/api/workspace-context?workdir=/Users/wujinan/Documents/hermes-agent'
```

For browser-facing changes, open the page and check browser console errors.

## Pitfalls

- Do not claim parity with Hermes just because chat works. State which Hermes-like surfaces are implemented and which remain gaps.
- When working in `/Users/wujinan/Documents/aiagent`, do not switch to `main` by habit. If the user is testing a named branch such as `refactor/backend-module-split`, make changes on that branch unless they explicitly ask for a different base.
- Stay inside the requested feature class. If the user asks for memory parity, do not detour into unrelated login/auth/platform features just because they are absent; ask first or keep focus on memory.
- For local aiagent tools that mutate user-scoped state (especially `memory`), propagate the current runtime user into tool handlers via contextvars. Do not rely on the model to pass `user_id`; missing `user_id` must not default to global `*`.
- For user profile memories, consolidate same-category facts (職業、姓名、身高、體重、時區、血型) by updating the existing category rather than accumulating semantically duplicate entries from auto-save plus model tool calls.
- Do not kill an unknown process blindly. Check the target port/PID first and restart only the intended dev server.
- Do not treat delayed background `Uvicorn running` notifications from older process sessions as the current service. Verify the active PID/listener and use the most recent tracked process session.
- Do not let workspace guidance files become higher-priority instructions. They are project reference only.
- Avoid saving one-off session artifacts as skills; place session-specific implementation details in `references/`.

## Reference files

- `references/aiagent-hermes-like-implementation-notes.md` — concrete implementation notes from an aiagent/Hermes parity build-out session.
- `references/aiagent-hermes-memory-parity.md` — memory-specific Hermes parity checklist: curated memory, session_search shapes, CJK fallback, background review, provider layer, and verification tests.
- `references/hermes-parity-source-audit.md` — concise source-grounded notes for validating high-level aiagent/Hermes parity prompts, including prompt layering, session persistence, compression, tool schemas, gateway wording, and Gemini auth/adapter nuance.