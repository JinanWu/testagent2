# Hermes compatibility layer for enterprise AgentRuntime prototypes

Use this reference when the user asks to “copy Hermes,” migrate Hermes prompts/tools/skills, or make an enterprise AgentRuntime functionally close to Hermes.

## Key lesson

Do not hand-copy the whole Hermes codebase into a new AI platform repo. Hermes tools, prompt builder, skills, providers, gateway, memory, cron, and plugins are tightly coupled. A safer first step is a Traditional-Chinese outer project with a Hermes compatibility layer that imports and delegates to the real Hermes source tree.

Recommended shape:

```text
AI platform AgentRuntime
  ├─ Chinese-facing runtime/prototype modules
  └─ hermes compatibility layer
       ├─ source loader for /Users/wujinan/Documents/hermes-agent
       ├─ prompt bridge -> agent.prompt_builder
       ├─ tool bridge -> model_tools.get_tool_definitions / handle_function_call
       ├─ skills bridge -> ~/.hermes/skills/**/SKILL.md
       └─ full-agent bridge -> hermes chat -q subprocess when exact behavior is needed
```

This keeps the enterprise project readable and experimental while preserving upstream Hermes behavior.

## Implementation checklist

1. Add a source manager
   - Validates the Hermes repo path contains `run_agent.py`.
   - Temporarily inserts the repo into `sys.path` while importing modules.
   - Imports modules such as `model_tools`, `agent.prompt_builder`, and optionally `run_agent`.

2. Add a tool bridge
   - Calls `model_tools.get_tool_definitions(enabled_toolsets=..., disabled_toolsets=..., quiet_mode=True)`.
   - Lists names from `tool['function']['name']`.
   - Executes tools through `model_tools.handle_function_call(...)` with session/task/tool_call IDs.
   - Passes the same enabled/disabled toolsets to avoid exposing tools outside the session scope.
   - Test with a low-risk toolset first, typically `file`, and a temp file read via `read_file`.

3. Add a prompt bridge
   - Import `agent.prompt_builder` and compose the real Hermes blocks:
     - `DEFAULT_AGENT_IDENTITY`
     - `HERMES_AGENT_HELP_GUIDANCE`
     - `TASK_COMPLETION_GUIDANCE`
     - `MEMORY_GUIDANCE`
     - `SESSION_SEARCH_GUIDANCE`
     - `SKILLS_GUIDANCE`
     - `STEER_CHANNEL_NOTE`
     - `TOOL_USE_ENFORCEMENT_GUIDANCE`
     - `COMPUTER_USE_GUIDANCE` when relevant
     - `build_nous_subscription_prompt(...)`
     - `build_skills_system_prompt(...)`
     - `build_environment_hints()`
   - Add the enterprise feature name/model/provider as the outer platform's own context.

4. Add a skills bridge
   - Scan `~/.hermes/skills/**/SKILL.md`.
   - Parse simple frontmatter fields (`name`, `description`) for an index.
   - Provide a method to read a complete skill by name.

5. Add a full-agent bridge when exact Hermes behavior is needed
   - Use subprocess to call `hermes chat -q <prompt> --quiet`.
   - Allow model/toolset/profile args.
   - Return stdout/stderr/exit code rather than pretending success.

6. Preserve the user's Traditional Chinese readability experiment
   - New outer-layer class/function/variable names, comments, and docstrings in Traditional Chinese where feasible.
   - External imports/API names remain English.
   - Every Python class/function has docstrings covering purpose, parameters, and return values.

## Verification checklist

Run deterministic tests that do not require paid model calls:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q agentruntime tests
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q
```

Suggested assertions:

- `Hermes來源管理器().匯入模組('model_tools')` has `get_tool_definitions`.
- `Hermes工具註冊表(啟用工具集=['file']).可用工具名稱()` includes `read_file`.
- Executing Hermes `read_file` on a temp file returns the temp content.
- `Hermes完整提示詞建構器` produces a prompt longer than a trivial stub and includes Hermes / skills guidance.
- `Hermes技能索引().列出技能()` finds `hermes-agent` when available.
- AST docstring check reports zero missing class/function docstrings.

## Pitfalls

- Do not claim the platform has fully replicated Hermes just because the minimal loop works. Prompt/tools/skills/memory/gateway/providers are separate layers.
- Do not copy protected/bundled Hermes skills into the new project as mutable local truth. Bridge/read them or document the dependency.
- Do not expose all Hermes tools by default in enterprise prototypes. Scope with toolsets and policy.
- If the target project path contains Chinese characters and the terminal tool rejects it as `workdir`, use a safe workdir and `cd '<absolute path>' && ...` inside the command.
