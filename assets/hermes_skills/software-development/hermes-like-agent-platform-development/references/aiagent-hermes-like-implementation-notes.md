# aiagent Hermes-like implementation notes

These notes capture reusable patterns from a Hermes-parity build-out in `/Users/wujinan/Documents/aiagent` using `/Users/wujinan/Documents/hermes-agent` as reference.

## Implemented surfaces in that session

- FastAPI app with `/api/chat`, `/api/chat/stream`, `/api/health`, `/api/features`.
- Gemini ADC client using env-configured project/location/model (`lab-cola-rd`, `global`, `gemini-2.5-flash-lite` in that environment).
- SQLite persistence for sessions, messages, runs, run events, and tool calls.
- Hermes tool bridge that sets contextvars before dispatch: task/session/run/tool-call IDs, enabled tools/toolsets, and current user task.
- Session APIs: list/create/read/rename/delete/fork.
- Run APIs: read run metadata and run events after an SSE disconnect.
- Slash commands: `/help`, `/new`, `/sessions`, `/resume`, `/title`, `/fork`, `/undo`, `/compress`, `/notifications`, `/history`, `/tools`, `/skills`, `/processes`.
- OpenAI-compatible minimal endpoints: `/v1/models`, `/v1/chat/completions`.
- Context compression: deterministic head + reference-only middle summary + protected tail, with `context.compressed` event.
- Hermes process notification drain: bridge `process_registry.drain_notifications()` into `process.notification` events and `/api/process-notifications`.
- Workspace context: scan `AGENTS.md`, `HERMES.md`, `CLAUDE.md`, `.cursorrules`; wrap as `[WORKSPACE CONTEXT — UNTRUSTED REFERENCE ONLY]`.
- Browser frontend with chat, session list, role/skill/workdir controls, events, process/tools/skills/capabilities/workspace panels.

## Durable code patterns

### Workspace context wrapper

Use an explicit wrapper that says project files are untrusted reference only. This is important because AGENTS/CLAUDE/HERMES files may contain natural-language instructions, but they must not override system/developer/latest-user instructions.

### Stable session system prompt

Create a session system prompt once and persist it. For later turns, append only current-turn overlays like selected skill or workspace context rather than mutating the cached historical prompt.

### Process notification drain

After tool execution and before final answer, drain Hermes process registry notifications and convert them to run events. Also expose a manual `/notifications` command and `/api/process-notifications` endpoint for debugging.

### Delayed watch-pattern notifications

Hermes may deliver `Uvicorn running` watch notifications from older process sessions after a later restart. Treat these as stale unless the PID/listener matches the currently active server. Always verify with `lsof`/health endpoint before reporting current service state.

## Verification recipe

```bash
AIAGENT_MODEL_MODE=fake python3 -m pytest -q
AIAGENT_MODEL_MODE=fake python3 -m py_compile 後端/服務.py tests/test_runtime.py tests/test_api.py
```

Then restart the server with the intended env vars and verify:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/v1/capabilities
curl 'http://127.0.0.1:8000/api/workspace-context?workdir=/Users/wujinan/Documents/hermes-agent'
```

If a frontend changed, load `http://127.0.0.1:8000/` in a browser tool and check the console for JS errors.

## Remaining Hermes-like gaps to consider next

When comparing aiagent to Hermes operation-by-operation, keep the gap list class-level rather than one narrow TODO per bug. The most important missing runtime surfaces observed were:

1. Approval/security flow: native `approval.requested`, approve/deny APIs, frontend approval cards, dangerous terminal/patch/write/process-kill pause/resume, yolo/admin bypass, and secret/path/URL safety guards.
2. Checkpoint/rollback: pre-edit snapshots for `patch`/`write_file`, `/snapshot`, `/rollback`, diff/hash tracking, and workdir/profile scope checks.
3. Native session search: FTS5 over aiagent's own SQLite messages, discovery windows, read/scroll APIs, and an aiagent-local `session_search` tool rather than only Hermes DB search.
4. Memory integration: aiagent-owned or provider-backed user/project memories injected into prompt, plus memory list/add/replace/remove APIs and redaction/de-duplication rules.
5. Tool output references: store large tool outputs in blobs, pass preview + `result_ref` into the model, and expose read/pagination APIs.
6. Real workdir semantics: persist workdir on sessions/runs, make terminal cwd and relative file tools resolve through it, and require approval for out-of-scope writes.
7. Clarify/pending input and mid-run steering: `clarify`, `/queue`, `/steer`, `/retry`, pending interactions, and run resume after user input.
8. Durable background systems: native cron scheduler, background prompt runner, standing goals, background process completion delivery, and active agents/tasks APIs.
9. Subagents and kanban: child run tracking, delegated task event mirroring, cancellation, background child result reinjection, and durable kanban boards/workers.
10. Extensibility: MCP client/tool discovery, plugin system, dynamic toolset management, richer skill lifecycle/curator-lite, and profile isolation.
11. Observability: doctor/debug/status diagnostics, usage/token/cost insights, provider retry/error classification, LSP diagnostics, and browser/CDP/dialog/vision/TTS/computer-use coverage.
12. API compatibility: token/tool-call streaming, usage fields, error schemas, and fuller OpenAI-compatible behavior.

For planning handoff into Apple Reminders, create backlog reminders with title prefixes such as `[P0][SP5] aiagent：...` and bodies using four sections: `任務背景` / `要執行的內容` / `預期產出` / `驗收標準`.
