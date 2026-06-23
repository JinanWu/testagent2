# Repo reconnaissance for API/example extraction

Use this when the user asks to "check the repo" and provide a concrete example of returned data.

Checklist:
1. Clone or sync the target repo/branch first.
2. Inspect branch layout with `git branch -a` and recent commits to find the active feature branch.
3. Read the entrypoint and service/parser files that define request/response shape.
4. Read README/API docs to confirm public examples and error semantics.
5. If the code and docs diverge, prefer the code for current behavior and mention the mismatch.
6. Keep example payloads compact: 1 success example + 1 error example is usually enough; avoid large dumps.
7. Preserve the response envelope exactly (`success`, `data`, `error`, `index`, etc.) and only populate representative fields.

Common repo pattern:
- README often lags behind code during active feature work.
- For batch endpoints, examples should reflect the element-level result shape, not just the top-level summary.
