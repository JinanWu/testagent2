# Hermes Tool Compatibility and Continuation Controller Notes

Use this reference when building a Hermes-like web AgentRuntime that should reuse Hermes operational capabilities instead of only copying tool/skill metadata.

## Tool catalog is not execution

A common pitfall is to copy Hermes tool names or toolset manifests into a new project and assume the agent can use them. This only gives the model vocabulary. To make tools executable, the runtime must register real handlers and expose only tools that pass availability checks.

Recommended compatibility bridge for a prototype:

1. Add the Hermes source path to `sys.path`, e.g. `/Users/wujinan/Documents/hermes-agent`.
2. Import Hermes `model_tools.get_tool_definitions` and `model_tools.handle_function_call`.
3. Resolve an appropriate Hermes toolset, usually `hermes-api-server` for a web/API product, not `hermes-cli` unless CLI-only interactive tools are acceptable.
4. For each returned tool schema, register an internal tool handler that delegates to `handle_function_call(tool_name, args, task_id=..., user_task=...)`.
5. Report the registered tool list in `/api/health` or an equivalent admin endpoint so catalog-only vs executable tools is visible.
6. Keep role/policy gating outside the bridge: admin may receive all registered tools; viewer roles should receive only safe tools such as web/search/read-only operations.

Important: Hermes `get_tool_definitions` already applies tool `check_fn` logic, so missing credentials/dependencies naturally hide unavailable tools. Do not persist a negative lesson like “tool X does not work”; capture the setup needed or the fact that check_fn gates availability.

## Web search setup pattern

Hermes web tools appear only when a web backend is available. For a local prototype with no API key, installing `ddgs` can make `web_search` available through Hermes' ddgs backend. Paid/managed backends such as Tavily, Exa, Parallel, Firecrawl, SearXNG, or Brave can be used when configured in Hermes.

Verification probe:

```python
import sys
sys.path.insert(0, '/Users/wujinan/Documents/hermes-agent')
from model_tools import get_tool_definitions, handle_function_call
names = [d['function']['name'] for d in get_tool_definitions(enabled_toolsets=['web'], quiet_mode=True)]
assert 'web_search' in names
print(handle_function_call('web_search', {'query': 'Vertex AI Gemini Flash Lite', 'limit': 2}, task_id='probe', user_task='probe'))
```

## Continuation behavior: Hermes-like does not stop at every text response

A minimal runtime often uses this loop:

```text
if model returns tool_calls: execute tools and continue
else: treat model text as final answer
```

That is too weak for Hermes-like behavior. Hermes normally ends a user turn when there are no tool calls and the model produced a real final answer, but it has recovery/continuation guards for common weak-model failures:

- tool-use enforcement: if the model says it will search/read/write/run/verify, it should call the corresponding tool rather than describe an intention;
- task-completion guidance: do not stop at a plan, stub, or “you can run…” when the user asked the agent to do the work;
- post-tool empty nudge: if tool results were just appended and the model returns empty or no useful visible answer, inject a synthetic continuation message telling it to process the tool results;
- thinking-only recovery: if reasoning exists but visible text is empty, retry/prefill a limited number of times;
- empty-response retry and fallback;
- finish-reason handling: if the provider reports length/incomplete/max-token termination, append a continuation prompt rather than treating the partial text as final;
- intermediate acknowledgment detection: if the answer is only “I’ll check/search/inspect…”, append a system/user nudge to continue and actually use tools.

For a web prototype, add a `回答狀態判斷器` / continuation controller before returning final text. It should classify model output as one of: `final`, `needs_tool`, `intermediate_ack`, `empty_after_tool`, `truncated`, or `blocked`. Only `final` should complete the run.

## Frontend perception

Even if the backend loop is correct, request/response UI makes long tool loops feel stalled. Prefer SSE/WebSocket run events:

```text
run.started -> model.started -> tool.started -> tool.completed -> model.started -> message.delta -> message.completed
```

This is separate from autonomous continuation, but it materially affects whether users feel they must keep prompting the agent.
