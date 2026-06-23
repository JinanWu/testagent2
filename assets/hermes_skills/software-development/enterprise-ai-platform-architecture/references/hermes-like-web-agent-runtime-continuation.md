# Hermes-like Web Agent Runtime: Tool Bridge, Continuation, and Streaming

Use this reference when building an internal web Agent platform that wants Hermes-like behavior but is not a direct Hermes fork.

## Durable lessons

### Catalog-only tools are not executable tools

Copying Hermes tool names or toolset manifests into a new platform is not enough. The runtime must register executable handlers and pass only executable tools to the model.

Good compatibility pattern:

1. Add the Hermes source path to `sys.path` from configuration, not a hardcoded hidden dependency.
2. Import Hermes `model_tools.get_tool_definitions()` and `model_tools.handle_function_call()`.
3. Select a web-appropriate Hermes toolset, usually `hermes-api-server` before `hermes-cli`.
4. Register only tools that pass Hermes `check_fn` into the new platform's runtime registry.
5. Expose load status in health checks: tool count, registered names, skipped names, and errors.
6. Keep a local low-risk bootstrap tool set for deterministic tests.

This makes tools actually callable while preserving Hermes' environment checks and handler behavior.

### Web search availability depends on Hermes backend checks

Hermes `web_search` / `web_extract` only appear when a backend passes `check_web_api_key()`. For a no-API-key local prototype, installing/configuring `ddgs` can make the DuckDuckGo backend available. For production, prefer an explicit enterprise-approved backend such as SearXNG, Tavily, Exa, Parallel, Brave, or Firecrawl and record it in config.

Do not write a persistent rule that "web tools are unavailable" just because credentials/packages were missing; capture the setup fix.

### Do not stop at the first no-tool text

A minimal loop that returns whenever the model has no `tool_calls` feels unlike Hermes. Hermes normally ends a user turn on a real final answer, but it also has recovery and continuation paths for:

- empty responses;
- tool-result followed by empty response;
- intermediate acknowledgements such as "I will search/check/run...";
- tool-use avoidance where the model describes actions instead of calling tools;
- truncation / `MAX_TOKENS` / incomplete finish reasons;
- safety or blocked responses;
- reasoning-only / thinking-only responses.

For a Hermes-like web runtime, add a continuation controller between model response and finalization:

```text
model response
  -> if tool_calls: execute tools and continue
  -> else classify response:
       final | empty | post_tool_empty | intermediate_ack | needs_tool | truncated | blocked
  -> if non-final and retry budget remains: append synthetic continuation prompt and continue
  -> else finalize with clear status/error
```

Recommended synthetic nudges:

- post-tool empty: "You already have tool results; process them and answer the user."
- needs-tool: "Do not stop at a plan or promise; call the required tool now."
- truncated: "Continue from where you left off without repeating earlier text."
- empty: "Produce a visible answer or call a tool if more work is needed."

### Prompt guidance matters

Add a compact Hermes-like operating brief to the system prompt:

- Complete the user's task; do not merely describe how you would do it.
- If you say you will search/read/write/run/verify, actually call the tool.
- Do not make "I can help" or "next you should" the final answer unless the user only asked for advice.
- After a tool call, read the result and either continue using tools or provide a verified final answer.
- If blocked by permissions, credentials, or external services, report the blocker and what was tried; never fabricate results.

### Web UX needs streaming and heartbeat events

Request/response chat makes long tool runs look stuck. Add SSE or WebSocket streaming for run events:

- `run.queued`
- `run.started`
- `prompt.built`
- `model.started`
- `tool.started`
- `tool.completed`
- `run.auto_continue`
- `run.still_working`
- `message.completed`
- `run.failed`

A simple implementation can run the synchronous runtime in a background thread and push events through a queue to `StreamingResponse(text/event-stream)`, emitting `run.still_working` every ~15 seconds when no event arrives.

## Verification checklist

Use fake models for deterministic tests:

1. Model first returns an intermediate acknowledgement, then final answer; assert `run.auto_continue` fires.
2. Model calls a tool, then returns empty, then final answer; assert post-tool continuation fires.
3. Model returns `finish_reason=MAX_TOKENS`, then final answer; assert truncation continuation fires.
4. Health endpoint reports the number and names of executable Hermes tools.
5. Live smoke test: `/api/chat` or SSE asks the model to call `web_search`; events show `tool.started` and `tool.completed` for `web_search`.
6. Live smoke test: low-risk `terminal` command such as `printf aiagent-terminal-ok` returns expected output.

## Pitfalls

- Do not expose dangerous Hermes tools to a web UI without role policy, audit logs, and approval/sandbox boundaries.
- Do not let synthetic continuation prompts become durable user messages unless intentionally stored; they are runtime scaffolding.
- If a provider returns duplicated text because both `resp.text` and parts are concatenated, choose one normalized extraction path.
- Tool schemas and enabled toolsets should be stable for a run. Changing them mid-run can confuse the model and break cache assumptions.
