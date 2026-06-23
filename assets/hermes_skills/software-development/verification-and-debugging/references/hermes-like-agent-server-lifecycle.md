# Hermes-like web agent server/process lifecycle notes

Use this reference when building or debugging a web-hosted Hermes-like agent that exposes Hermes terminal/process tools through its own runtime.

## Durable lessons

1. Exposing Hermes `terminal` and `process` tools is not enough for reliable server startup. Hermes' behavior depends on a lifecycle pattern: start long-lived servers with `terminal(background=true, watch_patterns=[...])`, retain the returned `session_id`/PID, inspect with `process`, and verify readiness with HTTP checks before final response.
2. Do not generate a fresh tool `task_id` for every delegated Hermes tool call. Use a stable key per web session or run, such as `aiagent-session-{session_id}` or `aiagent-run-{run_id}`, so cwd/environment/process associations remain coherent across terminal/process calls.
3. When wrapping Hermes `handle_function_call`, pass the full run context, not just `task_id`: `session_id`, `tool_call_id`, `turn_id`/run id, `enabled_tools`, and `enabled_toolsets`. Without this, tool-search bridges, execute-code tool scoping, process tracking, middleware, and approval observability can drift from the web runtime's actual authorization boundary.
4. Add explicit prompt policy for server startup:
   - inspect or choose a free port first;
   - avoid the port already used by the agent service itself;
   - run server/watch/dev commands in background mode rather than foreground;
   - wait for a readiness log pattern or poll process logs;
   - verify with `curl`/health/page request;
   - report URL, port, PID, and process session id.
5. Treat terminal guardrail errors that mention `background=true`, `long-lived`, `server`, `watcher`, or foreground background wrappers as actionable continuation signals. The runtime should automatically nudge/retry with background mode instead of accepting the tool error as final failure.
6. Web frontends consuming SSE may receive both internal runtime `message.completed` events and final response `message.completed` events. Distinguish them by payload shape (for example, final events include `status` and `answer`) to avoid replacing the answer with `失敗：unknown`.
7. Browser verification can show stale HTML/JS from cache. When checking a just-edited static frontend, reload with a cache-busting query such as `/?v=2` and inspect that the active script contains the expected new guard.
8. Hermes `search_files(target="files")` needs a file pattern. If a model asks to list a directory and omits `pattern`, normalize it to `pattern="*"` in the wrapper; otherwise an existing directory can incorrectly appear empty (`{"total_count":0}`). Add a regression test for this because it directly affects autonomous server discovery.
9. Persist session-level system prompts. Hermes builds the stable prompt once per session for prompt-cache stability; a web wrapper should store `system_prompt` on the session and only append per-turn extras (for example a manually selected skill) without overwriting it.
10. Provide session/run API surfaces, not only `/chat`: sessions list/read/rename/delete/fork, run read/events, tool/skill catalogs, capabilities, and a minimal OpenAI-compatible `/v1/chat/completions` endpoint make the wrapper behave like a real Hermes gateway rather than a single chat form.
11. Long sessions need a context-compression view before model calls. Preserve head and protected tail, summarize the middle with a `[CONTEXT COMPACTION — REFERENCE ONLY]` message, and keep the original DB transcript intact unless implementing full lineage rotation.
12. Drain Hermes `process_registry.drain_notifications()` after tool execution and before final response. Convert `watch_match`, `watch_disabled`, overflow, and completion events into the wrapper's own run events or pending notifications. Add a `/notifications` or `/api/process-notifications` endpoint so delayed watcher events can be inspected explicitly.
13. Stale watch-pattern notifications may arrive after a service has already been restarted. Final reports should identify the current authoritative process session/PID and explicitly label older `proc_*` watcher messages as delayed/stale when verified by port/PID.

## Minimal server-start algorithm for a Hermes-like web agent

1. If the user asks to start a server, choose a target port and check whether it is already listening.
2. If the agent itself is using that port, do not kill it; select another port or ask before replacing it.
3. Call terminal with `background=true` and a readiness `watch_patterns` value such as `Uvicorn running`, `Application startup complete`, `ready`, or the framework-specific equivalent.
4. Capture and persist `{process_session_id, pid, command, cwd, port, run_id, user_session_id}` in the runtime or event store.
5. Call `process(action='poll' or 'log')` after startup and then run a direct HTTP health/page check.
6. Stream lifecycle events to the frontend: `server.starting`, `server.ready`, `server.failed`, `server.stopped`.
7. Drain Hermes process notifications (`watch_patterns` and `notify_on_complete`) into the wrapper's run event stream and/or pending notification store.
8. Final response should include the URL, PID, process session id, verification endpoint/status, and how to stop the process.

## Recommended API/UI parity checklist

- `GET /v1/capabilities` with chat/session/run/tool/skill/process flags.
- `GET /v1/models` and minimal `POST /v1/chat/completions` for OpenAI-compatible UIs.
- Session APIs: list, create, read messages, rename, delete, fork/resume.
- Run APIs: read status and list events for reconnect after SSE loss.
- Tool/skill APIs: authorized tool schemas, skill list/search/detail.
- Process APIs: list by session task context, action endpoint, process notification drain endpoint.
- Slash commands: `/help`, `/new`, `/sessions`, `/resume`, `/title`, `/fork`, `/undo`, `/compress`, `/notifications`, `/processes`, `/tools`, `/skills`.
- Frontend panels: session list, chat, events, process inspector, tool catalog, skill browser, capabilities view.

## Pitfalls

- Foreground server commands will either hang the model turn or trigger Hermes terminal guardrails. Always use background mode for long-lived services.
- A web-hosted agent does not automatically inherit Hermes gateway's chat/thread watcher context. If the wrapper wants process completion/readiness notifications, it must bridge process events into its own SSE/session system.
- Reusing Hermes tools via `handle_function_call` is convenient, but wrapper code must provide stable session identity and lifecycle state; otherwise the result feels less capable than Hermes even though the same tools are present.
- Process endpoints invoked outside the runtime do not automatically inherit the runtime's ContextVars. Require a `session_id` (or equivalent owner key) and set the same Hermes task/session context before calling the `process` tool.
- Header-only role simulation is acceptable for a local prototype but not a production authorization boundary; admin-only terminal/process/write/patch actions need real auth/approval gates.
