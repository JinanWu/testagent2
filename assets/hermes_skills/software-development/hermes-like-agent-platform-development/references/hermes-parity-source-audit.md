# Hermes parity source-audit notes

Use this reference when a future task asks whether a planned aiagent/Hermes-like implementation prompt matches `/Users/wujinan/Documents/hermes-agent`.

## High-level facts verified from Hermes source

- The core runtime is `run_agent.AIAgent` with the main turn loop in `agent/conversation_loop.py`; `run_agent.py` now forwards many methods into focused `agent/*` modules.
- Session persistence is SQLite-backed via `hermes_state.py` (`state.db`) with sessions, messages, FTS, and stored `system_prompt` for prompt-cache stability.
- A turn begins in `agent/turn_context.py`: ensure/create DB session, sanitize input, restore/build cached system prompt, early-persist the user turn, run preflight context compression, then enter the model/tool loop.
- System prompt assembly is in `agent/system_prompt.py`, layered as:
  1. `stable`: identity, Hermes-help guidance, task/tool guidance, skill index, environment/platform/coding hints.
  2. `context`: caller system message and context files such as `AGENTS.md`, `HERMES.md`, `.cursorrules`.
  3. `volatile`: memory, user profile, external memory block, date/session/model/provider metadata.
- Tools are not simply text in the system prompt. Tool schemas come from `model_tools.py`, `tools/registry.py`, `toolsets.py`, and are sent with the LLM request. Tool guidance and skills index are prompt text.
- Context compression defaults to `threshold_percent=0.50` with a minimum-context floor. Hermes checks preflight rough tokens and later provider-reported/estimated prompt tokens; compression preserves head + protected tail and summarizes the middle as reference-only context.
- The internal agent loop mainly uses OpenAI-compatible `messages`/`tool_calls` as the canonical shape, while provider adapters/transports translate at the boundary.
- Gemini provider nuance:
  - `provider="gemini"` / Google AI Studio uses API-key auth (`GOOGLE_API_KEY` or `GEMINI_API_KEY`) and `GeminiNativeClient` over `https://generativelanguage.googleapis.com/v1beta`.
  - `google-gemini-cli` / Cloud Code Assist uses Hermes Google OAuth PKCE (`~/.hermes/auth/google_oauth.json`) and the Cloud Code Assist backend.
  - Plain gcloud ADC/Vertex AI Gemini is a valid enterprise aiagent adapter choice, but it is not the same as Hermes's current Google AI Studio API-key or google-gemini-cli OAuth paths. State this as a deliberate project adaptation, not as copied Hermes behavior.
- Tool-call loop nuance: Hermes appends the assistant tool-call message to working `messages`, executes tools, appends tool results, and continues the loop. User turns are early-persisted for crash resilience; assistant/tool messages are flushed to SQLite at persistence points. Do not describe this as "every tool call is written to SQLite before execution".
- Gateway nuance: Hermes has CLI/TUI/Desktop and a messaging gateway. A terminal-only MVP should be described as a CLI entrypoint/adapter, not as Hermes gateway being terminal-only.

## Recommended wording for prompts/specs

When asking an implementation agent to build aiagent parity, say:

> Preserve Hermes-style boundaries: CLI/gateway entrypoint → AgentRuntime turn setup → cached system prompt builder → context compression → provider adapter → LLM call → tool-call execution loop → SQLite session/runs persistence. Internal messages should use an OpenAI-compatible canonical shape; provider adapters translate at the boundary. For this project, Gemini may use company gcloud ADC/Vertex AI as a deliberate provider-adapter substitution, while prompt/session/tool/skill behavior should still mirror Hermes.

Avoid wording that implies:

- Hermes's messaging gateway is only a terminal interface.
- Hermes's Gemini provider is gcloud ADC/Vertex by default.
- Tool schemas live inside the system prompt text.
- Tool calls are always written to SQLite before execution.
