# Traditional Chinese Hermes-like AgentRuntime Prototype Pattern

Use this reference when the user asks to build a first AgentRuntime spine before adding enterprise platform features, especially when they want a Traditional Chinese codebase experiment.

## Durable pattern

For an MVP runtime inspired by Hermes, build a small vertical slice first:

```text
RunRequest
  -> create or load session
  -> persist user message immediately
  -> build system prompt + history + allowed tool schemas
  -> call normalized model client
  -> if tool_calls: validate/execute tools, persist tool call/result, append tool message, loop
  -> if no tool_calls: persist final assistant message and complete run
```

The minimum useful modules are:

- `核心/執行器.py`: Agent loop / run state machine.
- `核心/訊息.py`: request/result/message/tool-call dataclasses.
- `核心/事件.py`: run event collector.
- `提示詞/提示詞建構器.py`: platform + feature + allowed tool prompt.
- `模型/模型客戶端.py`: abstract normalized model interface plus fake model for tests.
- `模型/openai相容客戶端.py`: OpenAI-compatible Chat Completions adapter.
- `工具/工具註冊表.py`: tool schema + handler registry.
- `工具/工具執行器.py`: validate/dispatch tool calls.
- `儲存/sqlite儲存庫.py`: sessions/runs/messages/tool_calls/run_events.
- `tests/`: deterministic tests using a fake model, not a live API.

## Traditional Chinese code experiment

If the user explicitly wants Chinese-readable code:

- Use Traditional Chinese for self-owned class names, function names, variables, comments, and docstrings.
- Keep external imports, standard-library names, API field names, and provider protocol fields in their original language.
- Every Python class/function should have a docstring containing: purpose, parameters, returns, and important notes.
- Prefer simple, consistent Chinese terms over literal translation churn. Example mappings:
  - Runtime -> `智慧代理執行器`
  - Tool registry -> `工具註冊表`
  - Tool executor -> `工具執行器`
  - Model client -> `模型客戶端`
  - Run event -> `執行事件`
  - Turn context -> `回合上下文`

## Verification checklist

Run deterministic verification before reporting completion:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import ast
from pathlib import Path
missing=[]
for path in list(Path('agentruntime').rglob('*.py')) + list(Path('tests').rglob('*.py')):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node) is None:
            missing.append(f'{path}:{node.lineno}:{node.name}')
print('missing_docstrings=', len(missing))
for item in missing:
    print(item)
PY
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q
```

Also run a fake-model end-to-end demo that proves:

- status is `completed`;
- final answer is returned;
- model was called twice for a tool-call case;
- SQLite contains 1 session, 1 run, 4 messages, 1 tool_call, and run_events.

## Pitfalls

- If the project path contains CJK characters and the terminal tool refuses it as `workdir`, use a safe `workdir` such as the user home and run `cd '<absolute CJK path>' && ...` inside the command. Do not conclude the project itself is unusable.
- Avoid live model calls in tests. Use a fake model client to make the tool loop deterministic.
- Do not build Web UI, SSO, RAG, memory, cron, or multi-agent features in the first slice unless explicitly requested. Preserve the contracts and extension points instead.
