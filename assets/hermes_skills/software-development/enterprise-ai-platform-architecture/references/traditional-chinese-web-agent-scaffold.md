# Traditional Chinese Web Agent Scaffold Notes

Use this reference when building a Hermes-like web Agent project for a Chinese-reading team, especially when the user wants Python code readability tested in Traditional Chinese.

## Proven vertical slice

A compact first scaffold can be a single FastAPI service plus static frontend while preserving future service boundaries:

```text
frontend chat page
  -> POST /api/chat
  -> Gateway: session, user context, role/feature permission
  -> AgentRuntime: prompt + model + tool-call loop
  -> ToolRegistry: executable low-risk tools + catalog of future tools
  -> SkillLoader: read built-in skill Markdown snapshots
  -> SQLiteRepository: sessions, runs, messages, tool_calls, run_events
```

This is enough to prove the product flow before splitting into separate Cloud Run services.

## Traditional Chinese code conventions

- Use Traditional Chinese for self-owned Python class names, function names, variables, comments, and docstrings.
- Keep external protocol names in their original English form: HTTP headers, JSON fields, `role`, `tool_calls`, frontend JS/CSS naming, package imports.
- Require docstrings for every class/function. A quick AST verification should report `missing_docstrings= 0`.
- Use deterministic fake-model tests for the tool loop before trying a live model.

## Built-in Hermes skills/tools without unsafe exposure

When the user asks to "include Hermes skills and tools":

1. Copy or snapshot Hermes skills into an internal read-only directory such as `內建資料/skills/`.
2. Load Hermes tool/toolset metadata into a catalog file such as `內建資料/tools/hermes_tool_manifest.json`.
3. Expose only low-risk executable MVP tools at first, for example:
   - `now_time`
   - `calculate`
   - `list_skills`
   - `read_skill`
   - `search_tool_catalog`
4. Keep high-risk tools such as terminal/file/browser as catalog entries only until approval, workspace sandboxing, policy checks, and audit logging are implemented.

This satisfies the "built in" requirement while avoiding a web UI that can immediately run local filesystem or terminal actions.

## Gemini ADC verification pattern

For a Vertex AI / Gemini ADC setup:

- Use `google.genai.Client(vertexai=True, project=..., location=...)`.
- Do not add API key config when the user explicitly asks for ADC.
- Verify ADC separately with `gcloud auth application-default print-access-token`.
- Then verify the exact model id with a minimal `generate_content` call, because marketing/model-family names may not be valid Vertex publisher model ids in the chosen project/location.
- If a requested "flash lite" alias 404s, probe the current Vertex model id and update the scaffold default to the working flash-lite id rather than leaving a broken default.

## Verification checklist

Run all of these before reporting completion:

```bash
AIAGENT_MODEL_MODE=fake PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
import ast
from pathlib import Path
missing=[]
for path in list(Path('後端').rglob('*.py')) + list(Path('tests').rglob('*.py')):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node) is None:
            missing.append(f'{path}:{node.lineno}:{node.name}')
print('missing_docstrings=', len(missing))
for item in missing:
    print(item)
PY
```

Also start the app in fake mode and hit:

- `GET /api/health`
- `POST /api/chat` with a message that triggers `calculate`

The chat response should prove that the request flowed through Gateway -> Runtime -> tool loop -> final answer.