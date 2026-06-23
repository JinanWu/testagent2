# Message / request lifecycle tracing in a large Python agent codebase

Use this when the user asks “what happens after a message/request enters the system?” or wants an end-to-end architecture explanation grounded in source code.

## Workflow

1. Start from the public entrypoint for the surface in question.
   - CLI-style apps: input loop / command dispatcher.
   - Gateway/server apps: platform adapter handler, HTTP handler, or event callback.
   - For Hermes specifically, the gateway path starts at `gateway/run.py::_handle_message`, then `_handle_message_with_agent`, then `_run_agent`; CLI eventually calls `AIAgent.run_conversation` from `cli.py`.
2. Follow the handoff boundary into the core orchestrator.
   - Identify the function/class that all surfaces converge on.
   - In Hermes this is `run_agent.AIAgent.run_conversation`, forwarding to `agent/conversation_loop.py::run_conversation`.
3. Separate the explanation into layers:
   - Ingress / adapter handling: auth, command routing, session locks, queue/interrupt/steer behavior.
   - Turn preparation: history loading, context prompt, media/STT/vision enrichment, session metadata, persistence safety.
   - Core loop: system prompt/cache setup, message assembly, provider call, response validation, retry/fallback/compression.
   - Tool loop: tool-call validation, argument parsing, guardrails/middleware, execution, appending role=`tool` results, repeated model calls.
   - Finalization: result dict, transcript/session DB writes, hooks, memory/skill review, delivery to the user.
4. Read narrow slices around line hits instead of whole god-files.
   - Search for function names and call sites first.
   - Then read only the surrounding 100-400 lines needed to verify behavior.
5. When summarizing, include file/function anchors and distinguish “gateway-specific” from “core agent” behavior.

## Pitfalls

- Do not describe only the final core loop. Users asking about “message enters” usually need the adapter/session/command path too.
- Do not flatten retries, fallback, context compression, and tool execution into “calls the model.” These are separate phases and often explain real behavior.
- Watch for thin forwarders created by refactors. In Hermes, `run_agent.AIAgent.run_conversation` is a forwarder; the body lives in `agent/conversation_loop.py`.
- Large files often contain several entrypoints. Confirm the exact route for the surface being discussed before generalizing.
