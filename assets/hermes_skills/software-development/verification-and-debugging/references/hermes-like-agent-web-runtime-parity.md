# Hermes-like web agent runtime parity checklist

Use this reference when building or debugging a web-hosted Hermes-like agent that wraps Hermes tools but owns its own FastAPI/SSE frontend/runtime.

## Durable lessons from aiagent parity work

1. **Do not only expose Hermes tools; pass Hermes dispatch context.** When calling `model_tools.handle_function_call`, pass stable runtime context, not just `task_id`:
   - `task_id=f'aiagent-session-{session_id}'`
   - `session_id=session_id`
   - `turn_id=run_id`
   - `tool_call_id=call.id`
   - `enabled_tools=current_allowed_tools`
   - `enabled_toolsets=current_enabled_toolsets`
   This keeps Hermes tool-search, execute-code tool scope, process tracking, middleware/approval observability, and session correlation closer to native Hermes.

2. **Stabilize the session system prompt.** Hermes builds a session system prompt once and reuses it for prompt-cache stability. A web wrapper should persist `system_prompt` on the session row and only append per-turn extras such as selected skill text without overwriting the fixed prompt.

3. **Normalize common tool-call omissions at the wrapper boundary.** For Hermes `search_files` with `target='files'`, models may omit `pattern`. In Hermes this can yield `total_count: 0` even when files exist. The wrapper should default missing `pattern` to `'*'` for `target='files'` and regression-test this behavior.

4. **Give the web runtime real Hermes-like resources.** Beyond `/chat`, expose at least:
   - sessions: list/create/read/rename/delete/fork;
   - runs: read status and replay events for SSE reconnect;
   - tools/skills: schema/catalog APIs;
   - processes: list/action with session task context;
   - capabilities and `/v1/models` for client feature detection;
   - optionally `/v1/chat/completions` for OpenAI-compatible clients.

5. **Implement slash commands as a separate dispatcher before the LLM.** Start with `/help`, `/new`, `/sessions`, `/resume`, `/title`, `/fork`, `/undo`, `/history`, `/compress`, `/tools`, `/skills`, `/processes`, and `/stop`. Keep high-risk commands such as `/stop` conservative until process ownership and approvals are implemented.

6. **Add context compression before increasing history windows blindly.** Preserve head and protected tail, replace the middle with a `[CONTEXT COMPACTION — REFERENCE ONLY]` summary, and never delete the full DB transcript just to make the model prompt smaller. Emit `context.compressed` events and include `compressed`/`context_messages` metadata in `prompt.built`.

7. **Frontend parity is a management surface, not just a chat box.** A Hermes-like web UI needs session list/resume, run events, process panel with poll/log/kill actions, tools catalog, skills browser, and capabilities/settings tabs.

## Regression tests to add

- `search_files target=files` without `pattern` finds files by injecting `'*'`.
- Server foreground guard nudges model to use `background=true` and emits `server.starting`.
- Stable Hermes `task_id` is reused for multiple tool calls within a session.
- Sessions API: create, list, read, rename, fork, slash `/help` and `/sessions`.
- OpenAI-compatible `/v1/chat/completions` returns a `chat.completion` envelope.
- Context compression preserves head/tail and inserts `[CONTEXT COMPACTION — REFERENCE ONLY]`.

## Remaining parity gaps to consider next

- Drain Hermes process completion/watch notifications into the web session instead of relying on external Hermes notifications.
- Add human approval flow for dangerous terminal/write/patch actions.
- Add workspace context discovery (`AGENTS.md`, `HERMES.md`, `.cursorrules`) with prompt-injection guard and size limits.
- Add durable background jobs/cron UI if matching Hermes gateway/API server workflows.
- Replace textified Gemini tool results with native `function_response` parts for stronger multi-tool correlation.
