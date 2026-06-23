# Hermes-like aiagent memory / session search / approval debugging

Use this reference when the user says an aiagent conversation claimed it remembered something, cannot recall a prior session, or failed to trigger dangerous-operation approval.

## Reproduction-first DB inspection

Do not trust the assistant transcript alone. Inspect the aiagent SQLite store and compare the two sessions involved:

- `sessions`: user_id, system_prompt, updated_at
- `messages`: user/assistant/tool content and timestamps
- `runs`: status transitions
- `tool_calls`: whether the model actually called `memory`, `session_search`, `terminal`, etc.
- `run_events`: `prompt.built`, `tool.started`, `tool.completed`, `approval.requested`, `memory.auto_saved`
- `memories`: whether a claimed memory was actually persisted
- `approval_requests`: pending/executed/denied approvals and arguments

Failure pattern seen in aiagent: session A said “已記錄 77.4 公斤” but `tool_calls` had no `memory` call and `memories` had no corresponding entry. Session B then could not recall it. The root cause was not user error; it was a runtime/tool-use contract gap.

## Memory parity lessons from Hermes

Hermes uses a curated, profile-scoped memory design:

- `MEMORY.md` = agent notes: environment facts, project conventions, tool quirks, lessons learned.
- `USER.md` = user profile: preferences, communication style, workflow habits.
- Entries are small and separated by `§`.
- Memory is loaded into a frozen `_system_prompt_snapshot` at session start.
- Mid-session memory writes are durable on disk immediately, but do not rewrite the current system prompt; tool responses expose the live write, and the next session/prompt invalidation refreshes the snapshot.
- Memory entries are scanned before prompt injection; suspicious entries are replaced with a blocked placeholder in the prompt while staying visible for user cleanup.

When implementing aiagent memory parity, treat Hermes memory as a four-layer system, not just a `memory` table:

1. Built-in curated memory: tiny `USER PROFILE` / `MEMORY` blocks with usage headers, char limits, dedupe, substring `replace`/`remove`, threat scanning, file/DB write safety, and frozen session snapshots.
2. Session recall: `session_search` over the persisted session DB for large/unbounded history, including discovery/read/scroll/browse shapes and actual anchored messages.
3. Background review: periodic self-improvement review fork that is tool-whitelisted to memory/skills and can save durable user facts even if the foreground model only says “已記錄” without calling `memory`.
4. Optional external providers: a `MemoryProvider`/`MemoryManager` layer for semantic recall with `prefetch`, `sync_turn`, `queue_prefetch`, `on_session_switch`, `on_memory_write`, and fenced `<memory-context>` injection into the user message rather than mutable system prompts.

For aiagent fixes, distinguish a short-term correctness patch from final Hermes parity:

- Short-term: deterministic auto-save for high-confidence personal facts can prevent demo-breaking false “已記錄” claims. Include common CJK patterns for name, occupation/job/role, height, weight, timezone/location, language/format preferences, “請記住…”, and “以後不要…”.
- Final parity: explicit memory tool contract, target stores (`user` vs `memory`), char limits, threat scanning, declarative-fact guidance, frozen prompt snapshot / controlled invalidation, background review, and session_search-backed recall rather than uncontrolled prompt drift.

## Session search parity lessons from Hermes

Hermes `session_search` is not just keyword snippets. It supports four shapes:

1. Discovery: `query` returns deduped sessions with snippet, match id, bookend_start, an anchored message window, and bookend_end.
2. Scroll: `session_id + around_message_id` returns a larger local window.
3. Read: `session_id` dumps a bounded whole-session view.
4. Browse: no query returns recent sessions.

For Chinese aiagent data, SQLite FTS5 may return no useful hits without throwing an error. Always combine FTS with a LIKE fallback for CJK terms, dedupe by message id, and rank user messages / numeric matches higher for personal-data queries such as weight.

## Dangerous approval parity lessons from Hermes

Hermes gates dangerous terminal commands in `tools/approval.py`, with:

- hardline blocklist that cannot be bypassed even by yolo (`rm /`, home/root/system recursive deletion, mkfs, block-device writes, fork bomb, shutdown/reboot, kill-all)
- dangerous patterns that require approval (`rm -r`, chmod/chown broad mutations, SQL destructive operations, systemctl lifecycle, curl|sh, find -delete, git reset/clean/force push, docker lifecycle, sensitive file writes)
- per-session approval state via contextvars to avoid gateway concurrency leaks
- YOLO mode frozen at import time so a tool call cannot flip an env var mid-run and bypass approval
- optional write approval for memory/skill mutations

If the model refuses before calling a dangerous tool, approval never gets a chance to run. For Hermes-like runtimes, combine prompt guidance (“call the tool; approval layer will pause”) with deterministic routing for explicit destructive user intents such as deleting `/Users/...` paths.

## Additional failure modes observed

### Gemini turns memory actions into tool names

In aiagent, Gemini may emit a function call named `add` when it intended `memory(action="add")`. This surfaces as:

```text
PermissionError: 工具未授權：add
```

Do not treat this as a user permission problem. Add a runtime normalization step before the allowlist check:

- if `call.name in {add, replace, update, remove, delete, list}` and `memory` is available, rewrite to `memory` and set `args.action` to the original function name
- keep a regression model that deliberately returns `工具呼叫(..., "add", {...})`
- also add deterministic auto-save for high-confidence personal facts used in user tests, such as weight/height, so a demo does not depend entirely on model tool-call discipline
- dedupe identical memory entries on `(user_id, target, content)` to avoid repeated auto-save prompts polluting the memory block

### Natural-language approval must bypass the model loop

When a session has a pending approval and the user says “我批准” / “我拒絕”, handle it before calling the model. Otherwise the model may re-issue the same dangerous tool call and create another pending approval, making the UI look like there is no way to approve.

Implementation pattern:

- at the start of a run, after storing the user message and computing allowed tools, check for approval phrases
- for approve: read the latest pending approval for the session and execute it directly
- for deny: mark the latest pending approval denied directly
- emit `approval.executed` / `approval.denied` events and store the assistant-visible result
- include both UI and text instructions in the waiting message: “Approvals 面板按允許/拒絕，或輸入我批准/我拒絕”

### Hermes terminal approval bridge needs `force=True`

aiagent has its own approval layer, but Hermes `terminal` also has an internal dangerous-command gate. If an approved command is executed via `model_tools.handle_function_call`, Hermes may still return:

```text
BLOCKED: User denied this command. The user has NOT consented to this action.
```

For approved terminal calls delegated to Hermes, call `tools.terminal_tool.terminal_tool(..., force=True)` directly instead of going through the schema handler, because `_handle_terminal` intentionally does not expose `force` from args. Keep the original approved arguments for audit, but add/record `force=True` in the executed tool-call record.

### Frontend approval discoverability

When `approval.requested` or a final `waiting_approval` result arrives over SSE, the frontend should automatically refresh approvals and switch/show the Approvals panel. Otherwise the backend is technically correct but users experience “it keeps asking for permission and there is no interface”.

## Verification gates

For this bug class, add regression tests before committing:

- claimed memory writes are persisted or auto-saved
- existing sessions receive or can retrieve the updated memory via tool/preload behavior
- Chinese session search for `體重` finds the session containing `77.4`
- Gemini-style memory action calls named `add` are rewritten to `memory(action="add")` before the allowlist check
- high-confidence personal facts such as `我的身高170公分` are auto-saved and deduped
- destructive delete requests become `waiting_approval` and create a pending terminal approval, without executing the command
- natural language “我批准” executes the latest pending approval instead of creating a second pending approval
- approved Hermes terminal calls use `force=True` so the internal Hermes dangerous-command gate does not block after aiagent approval
- the frontend exposes approval actions clearly when `waiting_approval` occurs
- browse mode returns a session count if the UI/model needs to answer “多少 session”

Run `py_compile`, full pytest, docstring/Traditional Chinese naming checks, and one live API verification on the relevant branch. For this user’s aiagent work, branch from `main` per item and use Traditional Chinese commit messages.
