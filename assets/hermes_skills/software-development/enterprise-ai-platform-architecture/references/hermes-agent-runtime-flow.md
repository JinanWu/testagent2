# Hermes Agent Runtime flow reference

Use this reference when the user asks how Hermes' Agent runtime actually works before designing an internal AI platform. It condenses a code walk of `/Users/wujinan/Documents/hermes-agent` around `run_agent.py`, `agent/conversation_loop.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `agent/tool_executor.py`, `model_tools.py`, `tools/registry.py`, `agent/system_prompt.py`, and `agent/chat_completion_helpers.py`.

## One-line model

Hermes Agent Runtime is a state machine that uses `messages` as state, LLM responses as decisions, tools as executable capabilities, and a loop to continue until a final answer or stop condition.

```text
user message
  -> build turn context
  -> build system prompt + message history + allowed tool schemas
  -> call model
  -> normalize response
  -> if tool_calls: validate + execute tools + append tool results + loop
  -> else: final answer
  -> finalize turn / persist / cleanup / return result
```

## Core files and responsibilities

- `run_agent.py`
  - Defines `AIAgent`, but many large methods are now thin forwarders.
  - Holds runtime state: model/provider/api_mode, tools, valid tool names, session id, session DB, callbacks, token/cost counters, interrupt state, fallback chain, guardrails, checkpoints.
- `agent/agent_init.py`
  - Implements `AIAgent.__init__` as `init_agent()`.
  - Loads tools via `get_tool_definitions(...)`, creates `valid_tool_names`, configures provider, fallback, session, memory, compression, callbacks.
- `agent/conversation_loop.py`
  - Implements `run_conversation()`.
  - Owns the per-turn loop: prepare API messages, call provider, normalize response, execute tool calls, continue or finalize.
- `agent/turn_context.py`
  - Per-turn prologue: create session row, sanitize user message, create task/turn ids, append user message, restore/build system prompt, early persist, preflight compression, plugin/memory context.
- `agent/system_prompt.py`
  - Builds prompt in `stable`, `context`, `volatile` tiers and caches it per session for prompt-cache stability.
- `agent/chat_completion_helpers.py`
  - Builds provider-specific request kwargs, calls model, normalizes assistant messages, handles provider quirks/fallback support.
- `agent/tool_executor.py`
  - Validates and executes tool calls, sequentially or concurrently. Handles guardrails, plugin blocks, checkpoints, callbacks, result persistence, and appending `role=tool` messages.
- `model_tools.py` + `tools/registry.py`
  - Tool schema retrieval and dispatch. Tools register with `registry.register(name, toolset, schema, handler, check_fn, ...)`; dispatch calls the handler and returns JSON-ish result text.
- `agent/turn_finalizer.py`
  - Post-loop finalization: max-iteration summary, cleanup, session persistence, output transforms, memory sync, result dict assembly, hooks.

## Turn context flow

`build_turn_context()` runs once per user turn:

1. Ensure session DB row exists.
2. Set live provider/model context for auxiliary calls.
3. Sanitize user input.
4. Create `effective_task_id` and `turn_id`.
5. Reset per-turn retry, guardrail, interrupt, and streaming state.
6. Copy conversation history and append current user message.
7. Restore cached system prompt from session DB or build it once.
8. Persist user message early for crash resilience.
9. Preflight context compression if needed.
10. Run `pre_llm_call` hooks and memory prefetch.

Enterprise-platform mapping:

```text
RunRequest -> RunContext
  -> create run_id/turn_id
  -> load session history
  -> persist user message immediately
  -> load UserContext + Feature + Skill
  -> prepare model/tool context
```

## System prompt shape

Hermes builds one cached prompt per session:

- `stable`: identity, tool guidance, memory/session/skill guidance, environment/platform hints, model execution guidance.
- `context`: user-supplied system message and project context files such as `AGENTS.md`.
- `volatile`: memory/user profile/external memory block, date/session/model/provider line.

Design lesson: keep the platform and skill prompt stable; inject per-turn context such as RAG/user context separately so prompt caching and mental model stay stable.

## Main loop shape

Inside `run_conversation()`:

1. Prepare `api_messages` from internal `messages`.
2. Repair malformed message/tool-call structures.
3. Inject ephemeral memory/plugin context into the current user message, not the cached system prompt.
4. Add system prompt and tool schemas.
5. Build provider-specific `api_kwargs`.
6. Call model through interruptible streaming/non-streaming path.
7. Normalize provider response into a uniform assistant message.
8. If `assistant_message.tool_calls` exists:
   - validate tool names and repair obvious hallucinated names;
   - validate JSON arguments;
   - dedupe and cap risky tool batches;
   - append assistant tool-call message;
   - execute tools;
   - append each tool result as `role=tool`;
   - continue the loop.
9. If no tool calls:
   - treat content as final answer;
   - append assistant final message;
   - break.
10. Finalize and return result.

## Tool execution chain

Minimum chain:

```text
assistant tool_call
  -> agent.tool_executor
  -> model_tools.handle_function_call
  -> tools.registry.dispatch
  -> tool handler
  -> tool result
  -> role=tool message
  -> next model call
```

Important runtime gates:

- Tool names must be in `agent.valid_tool_names`.
- Tool arguments must parse as JSON.
- Plugin/policy hooks and tool guardrails can block execution.
- File/terminal mutation tools may trigger checkpoints before execution.
- Long or large tool results may be persisted/truncated before being sent back to the model.
- Progress callbacks emit `tool.started` / `tool.completed` style signals.

Enterprise-platform mapping:

```text
ToolCall
  -> validate schema/name
  -> PolicyDecision for tool/data scope
  -> ToolExecutor/ToolAdapter
  -> ToolResult
  -> AuditEvent + ToolCall record
  -> append to model context
```

## What to copy conceptually

Copy these patterns:

1. Agent loop as a state machine, not a single LLM call.
2. Runtime-controlled allowed tool schemas.
3. Tool calls are model requests, not direct execution.
4. Normalize provider responses before the rest of the runtime sees them.
5. Persist the user message and intermediate tool/run events before final success.
6. Emit progress events for frontend display.
7. Treat invalid tool calls as recoverable by feeding structured tool errors back to the model.

## What not to copy for MVP

Avoid copying these until the platform has a working vertical slice:

- Multi-provider quirk matrix and every adapter.
- Prompt caching optimizations.
- Context compression engine.
- Profiles/plugins/gateway breadth.
- Local terminal/browser/checkpoint assumptions.
- Subagent delegation.
- Tool search bridge.
- Full fallback provider chain.
- Skill self-improvement loops.

## Minimal internal-platform runtime

For MVP-0, use this reduced shape:

```text
POST /runs
  input: session_id, user_id, feature_id, message, attachments[]

agent-runtime:
  1. load user + session history
  2. resolve feature -> skill
  3. resolve allowed tools
  4. persist user message and run.started
  5. build prompt/model request
  6. call LLM
  7. if tool call: validate, policy check, execute, audit, loop once/multiple times
  8. persist final assistant message
  9. emit run.completed
```

Minimum event set:

- `run.started`
- `message.received`
- `skill.selected`
- `model.started`
- `model.delta`
- `model.completed`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `message.completed`
- `run.completed`
- `run.failed`

Minimum tables:

- `sessions`
- `messages`
- `runs`
- `run_events`
- `tool_calls`

## User-facing planning guidance

When the user feels overwhelmed by the full platform size, shrink the answer to the Hermes runtime spine:

```text
Feature / Skill -> Prompt -> Model -> optional Tool -> Event -> Audit -> Final Answer
```

Recommend a modular monolith first: separate modules and contracts in the repo, but deploy only `web-app`, `api-agent-service`, and `postgres` for the first vertical slice. Split Cloud Run services after the boundaries stabilize.