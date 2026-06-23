---
name: verification-and-debugging
description: "Use when you need root-cause debugging, TDD, pre-commit verification, or interactive debugger workflows across Python and Node."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [debugging, testing, verification, tdd, pdb, debugpy, node-inspect, code-review]
    related_skills: [hermes-agent, plan]
---

# Verification and Debugging

## Overview

This umbrella combines the main software-verification loop with practical debugger workflows. It covers root-cause analysis, test-first fixes, pre-commit review, and the common debugger entry points for Python and Node.

## When to Use

- A bug or failing test needs systematic investigation before fixing.
- You need to write the regression test before code.
- You want a pre-commit quality gate on changed files.
- You need to inspect Python or Node state interactively.

## Core Workflow

### 1) Understand the failure

- Read the error carefully.
- Reproduce it reliably.
- Trace data flow to the point where values go wrong.
- Compare with nearby working patterns.

### 2) Prove the fix

- Write or update the test first when behavior changes.
- Keep the failing case minimal.
- Fix the root cause rather than masking symptoms.
- Re-run the relevant test and then a broader verification pass.

### 3) Review before commit

- Scan for obvious security and correctness issues.
- Check for new regressions and missing tests.
- Make sure the diff is clean before pushing.

## Debugger Entry Points

### Python
- Use `breakpoint()` for local interactive debugging.
- Use `python -m pdb` for script-level stepping.
- Use `debugpy` or remote attach for long-running or headless processes.

### Node
- Use `node inspect` for quick CLI debugging.
- Use the inspector/CDP path for automated or remote debugging.
- Prefer source-local, reproducible breakpoints over scattered logging.

## Traditional Chinese Python codebases

When the user asks to build or maintain a Python project with Traditional Chinese code identifiers:

1. Keep Python identifiers readable, not merely translated. Prefer verb + object for functions/methods (for example `建立批准請求`, `讀取檔案快照`, `還原檔案快照`) and clear nouns for classes/data objects.
2. Preserve external contracts in English: HTTP paths, JSON fields, SDK parameter names, provider schemas, OpenAI-compatible fields, and third-party import/API names should not be localized if callers depend on them.
3. Be careful with broad automated renames. They can corrupt library APIs such as `datetime.now`, `operator.sub`, `re.sub`, `google.genai`, SDK fields like `text`, and protocol strings such as `text/event-stream`. After renaming, run `py_compile`, full pytest, and a small AST/docstring/naming check.
4. Tests may keep required framework prefixes such as `test_`, but the semantic part should remain readable Traditional Chinese when the codebase convention requires it.
5. For aiagent/Hermes-like feature work, treat each backlog item as an isolated feature branch: verify the current branch/worktree, branch from the intended base before editing, keep the branch scoped to that feature, run the project gates, then commit and push the branch rather than mixing unrelated items on `main`. For this user's aiagent repo, write commit messages in Traditional Chinese.

## Common Pitfalls

1. **Guessing before tracing.** Debugging starts with evidence.
2. **Skipping the failing test.** TDD only counts if the test failed first.
3. **Trusting the summary alone.** Verify with real diffs and test output.
4. **Using the wrong debugger.** Pick Python vs Node based on the runtime actually failing.
5. **Letting docs drift from code.** For ML inference repos, verify README/schema claims against the actual label list, return shape, and fallback behavior.
6. **Silent fail-open behavior.** If prediction errors become default booleans or `unknown` values, confirm downstream ETL consumers can distinguish them from legitimate model output.

## ETL / data-pipeline environment verification

When validating an ETL or model-inference pipeline that intentionally mixes environments (for example: production API/source data but development BigQuery/sink, or a development model endpoint against production-shaped payloads), make the environment boundary explicit before any write or API call:

1. Inspect the repo entry points and config precedence first: README, env examples, CLI flags, config resolver, API endpoint resolver, and sink writer.
2. Verify branch/worktree before touching files, especially under the user's `/Users/wujinan/Documents` repos. If the user says not to touch git, do not run git-changing commands; status/diff checks are okay only when needed for safety.
3. When the task folder is a reminder-named wrapper path containing spaces, shell metacharacters such as `[` / `]`, or non-ASCII/CJK characters, avoid passing it as a terminal `workdir` if the tool rejects it. Use a safe `workdir` such as the user home and run `cd '<absolute repo path>' && ...` inside the shell command for verification, while file tools can still use absolute paths directly.
4. Prefer command-line overrides or temporary environment variables for the test run instead of editing `.env`, so the final command visibly proves `prod source -> dev sink`.
4. Run local non-writing checks first: dependency install, import/compile checks, doctests/unit tests, and API dry-run if available.
5. Before a real write, identify the exact target project/dataset/table and stage/report tables. If the sink is unknown, stop and ask; do not guess a dev BigQuery table.
6. Start with a small date/window or sample-sized run before default ranges, to limit prod API traffic, Vertex/LLM cost, and BigQuery writes.
7. In the report, include counts and routing evidence: source env/API URL class, sink project/dataset/table, stage input/output/update counts, skipped counts, errors, runtime, and whether prod was read-only.
8. For long-running ETL on the user's Mac, wrap the actual foreground command in `caffeinate -dimsu` so the machine stays awake until the process exits, run it as a tracked Hermes background process with `notify_on_complete=true`, and write output to a timestamped log via `tee`.
9. If the user needs progress reports, create a self-contained script under `~/.hermes/scripts/` that reads a state file containing run_id, PID, log path, start time, and exit marker, then schedule it with `cronjob(..., schedule='every 15m', no_agent=true)`. Report PID, elapsed time, log path, latest log line, current stage, and whether `caffeinate` is active. Remove the progress cron as soon as the main job finishes.
10. After completion, verify the sink directly with a read query against the written run_id/date range, not just the process exit code. If one auth path fails transiently (for example `bq` CLI reauth), use another already-authenticated project path such as the repo's Python BigQuery client/ADC, but report the auth caveat separately.

Pitfall: `--dry-run` semantics can differ by stage. Read the implementation before interpreting a dry-run as end-to-end proof; some stages may skip sink reads as well as writes.

Pitfall: some ETL summary stages do many serial LLM calls and may appear silent if node-level progress logging is missing. Estimate call count from chunking logic before promising runtime, give a conservative observation window, and prefer adding per-node progress logs/timeouts for production hardening.
Pitfall: `--dry-run` semantics can differ by stage. Read the implementation before interpreting a dry-run as end-to-end proof; some stages may skip sink reads as well as writes.

Pitfall: Summary-generation hangs can mask a completed metrics calculation. Check logs for the boundary between data load/metrics and LLM summary; if metrics are complete but summary has no per-node progress, avoid blind reruns. Add or recommend timeout, per-node logging, skip-summary/metrics-only mode, and fallback behavior so the report can still be written.

### Long-running LLM summary/report stages

When an ETL stage includes many LLM summary calls before writing the final report, treat scheduling and observability as part of the deliverable:

1. Estimate call count from code before launching or immediately when the user asks how long it will take: leaf batch calls from the configured chunk size, plus leaf merge calls and parent/branch/root summary calls.
2. Convert call count into an ETA range using plausible per-call latency, and give operational checkpoints (for example: still plausible at 30 minutes, suspicious after 2 hours, stop/rework after 2.5 hours).
3. If logs do not emit per-node progress, explicitly warn that quiet logs do not distinguish healthy work from a stuck SDK call; recommend adding node start/end logs before another full run.
4. For user planning, report target env/project/table, date window, expected model-call count, ETA range, log path, and stuck/healthy criteria.
5. Prefer background execution with completion notification for bounded long runs, but do not start a multi-hour job without giving the user the duration expectation if they need to schedule around it.
6. If the user says not to touch git, avoid git commands and branch/commit operations entirely; continue with runtime execution, logs, and BigQuery verification only.

For recurring mixed-env ETL validation patterns, including dev BigQuery sinks fed by production-shaped/source data, staging-table backfills, metrics-only report validation, and separating metric computation from LLM summary hangs, see `references/mixed-env-etl-dev-prod-validation.md`.

## Model-inference repo review verification

When reviewing a candidate model-serving repo or microservice for ETL integration:

1. Find the actual active repo copy first; task folders may contain multiple mirrors or migration copies. Identify the one with the current entrypoint before judging behavior.
2. Read the runtime entrypoint, model loader, and request parser before trusting README examples.
3. Compare documented output cardinality with the live constants in code; label-count mismatches are a frequent source of downstream schema drift.
4. Check for request guards on missing/invalid JSON, length checks on every parallel array, and explicit handling of model/API failures.
5. Watch for silent fallback paths that convert prediction errors into all-false, empty, or default labels. These need explicit downstream contract handling and alerting.
6. Verify with `py_compile` or equivalent syntax checks even when the code is mostly data plumbing; this catches import and syntax regressions quickly.

See `references/model-inference-review.md` for a concise checklist and concrete failure patterns from a passenger-survey sentiment/repo review.

## Schema-preserving model replacement

When replacing a model inside an existing inference/ETL service, first trace how downstream consumers transform the current output. Prefer an adapter that preserves the public API/BigQuery/dashboard schema when downstream formulas can be satisfied mathematically, instead of introducing new fields that force coordinated changes across repos.

1. Inspect downstream consumers before changing the serving contract: API response shape, ETL field names, score formulas, dashboard display rules, and any sentinel values such as `0` meaning missing.
2. Define the new model’s internal representation separately from the legacy contract. Example: internal `mood_index` 1–100 can be converted back to legacy `Sentiment_Label` / `Sentiment_Score` if the downstream formula is known.
3. Encode the adapter as a small pure function and test boundary cases: positive side, negative side, neutral midpoint, minimum/maximum clamps, and any sentinel-avoidance rule.
4. For LLM-backed replacements, add robust JSON extraction/normalization, clamp numeric ranges, and use a conservative fallback that downstream systems can distinguish or at least interpret safely.
5. Keep the existing public method name/signature if callers depend on it, but update docstrings/README to state that old fields may now be compatibility fields rather than model confidence.
6. Remove obsolete model dependencies and container build steps only after verifying no runtime import path still depends on them.
7. Verify with unit tests using a fake model/client, `py_compile`, import/startup checks with representative environment variables, and `git diff --check`.

Pitfall: changing `Sentiment_Score` from confidence to compatibility score is a semantic change even if the schema is unchanged. Document it explicitly so future analysts do not interpret the score as model confidence.

## Frontend auth redirect and route-target changes

When changing where users land after login in a React Router frontend:

1. Trace the route table before editing. Confirm which component is mounted at `/`, `/dashboard`, and any protected routes; route names can be misleading (for example a `DashboardPage` component may actually be the Q&A/task page while `/dashboard` renders the analytics dashboard layout).
2. Inspect the login page for both redirect paths: already-authenticated users and post-submit login success. Update both if the desired landing page changes.
3. Do not assume backend changes are needed unless the login response supplies a server-directed redirect or role-based target. If navigation is implemented with `useNavigate` / `<Navigate>`, keep the task scoped to frontend.
4. Verify with a focused source scan for remaining `navigate('/')` / `<Navigate to="/">` login redirects, then run the frontend gates (`npm run lint`, `npm run build`). If local `node_modules` is missing, run `npm ci` from the frontend package before the gates rather than treating `eslint: command not found` as a code failure.
5. Report the final branch, changed file(s), exact redirect target, and distinguish pre-existing lint warnings from new errors.

## Dashboard route-switch state preservation regressions

When fixing frontend regressions where filters, labels, buttons, or other API-derived controls disappear after switching dashboard tabs/routes and then reappear only after an API call returns:

1. Treat route unmount/remount as the primary suspect before changing backend APIs. Inspect whether the page initializes local state to empty arrays/maps and refetches on every mount.
2. Preserve small, stable API-derived definitions (for example label groups, label maps, key-to-group maps) in a module-level cache or shared store, and initialize component state from that cache so remount renders immediately with the last known definitions.
3. Coalesce repeated in-flight definition requests with a shared promise to avoid duplicate API calls when users switch tabs quickly.
4. Keep the first-load behavior unchanged: fetch definitions when cache is empty, build derived lookup maps in one helper, then write both local state and cache after success.
5. Guard async effects with an `ignore`/cancel flag so late responses do not set state after unmount.
6. Verify with lint/build plus a diff review that only the affected frontend page/store changed; do not introduce backend changes for a frontend remount/cache regression unless tracing proves the API is at fault.

Pitfall: do not cache user-specific search results or large paginated rows unless the task explicitly asks for result preservation. For this class of bug, the low-risk target is usually compact reference data that controls rendering of buttons/chips.

## Dashboard search/filter cost-control changes

When implementing UI/API filters that reduce backend query cost (for example BigQuery-backed semantic/vector search date windows):

1. Enforce the invariant in the backend service layer first, not only in the UI; frontend checks are a usability layer, backend checks are the cost/safety boundary.
2. Keep backend defaults and validation aligned with the SQL defaults. If SQL uses `CURRENT_DATE('Asia/Taipei')`, normalize missing dates in Python with the same timezone before validating span limits.
3. Use calendar-month arithmetic for requirements stated as “最多三個月”; include month-end clamp cases in tests (for example Jan 31 → Apr 30 allowed, May 1 rejected).
4. Update the API contract and endpoint docstrings when query parameter semantics change, especially error behavior such as 422 invalid range.
5. For frontend date pickers, constrain selectable min/max values and also re-validate on submit before firing the API request, because state can be stale or manually altered.
6. For dashboard tag/filter buttons that need both server-side query support and front-end instant filtering, keep the API parameter path intact for explicit searches, but derive a separate `visibleItems`/`visibleTotal` from the currently loaded rows for immediate UI response. Compare active UI filters against applied/server filters so counts are not mislabeled: show server `total` when filters match the API request, and local filtered count only when the user has changed filters after results loaded. Empty-state rendering should use `visibleItems.length`, not raw `items.length`.
7. If the user warns that ports may be occupied, prefer static verification (`pytest`, `py_compile`, `npm run build`, `npm run lint`, `git diff --check`) before starting local servers; only run servers when browser/manual verification is necessary, and inspect ports first.

## Dashboard optimized-hierarchy detail regressions

When a dashboard uses a fast/initial hierarchy API for performance and a leaf-level view is missing heavy text fields, do not blindly put the heavy fields back into the initial payload. First check whether the field was intentionally omitted by an API efficiency change, then prefer a leaf-detail lazy load with frontend caching and a small loading/error state. For FastAPI detail routes whose ids are hierarchical paths, use a path converter such as `{id:path}` and verify with a slash-containing id. See `references/optimized-hierarchy-leaf-detail-regressions.md` for the recipe and regression-test shape.

## Frontend UI cleanup verification

When executing small dashboard/frontend cleanup tasks such as hiding unfinished navigation entries or removing obsolete buttons:

1. Verify the user's provided folder may be a task wrapper, not the repo root. If the top-level folder is not a git repo, inspect likely child repos and report which repo actually changed.
2. Check branch/worktree status before edits, especially under `/Users/wujinan/Documents`; if the expected feature branch already exists, continue there rather than creating a duplicate branch.
3. Prefer minimal UI changes: remove the visible nav/button entry and any now-unused icon/handler code, but do not delete routes, pages, API methods, or deep-link structures unless explicitly requested.
4. After removing a UI control, adjust nearby layout classes so the deleted control does not leave an empty gap or awkward justification.
5. For bounded date/range filters, avoid hard interlocking DatePicker `min`/`max` constraints that force users to change fields in a specific order. Prefer allowing free date selection, deriving validation state, disabling the submit/search button when invalid, and placing a concise helper/error message next to the action (for example: “搜尋跨度不可超過三個月”). Keep a defensive submit-handler guard as well.
6. Verify with the project’s actual frontend gates, typically `npm run lint` and `npm run build`, and separately grep/check that the removed labels and obsolete handlers are no longer present in the changed files.
7. For dashboard table/detail field changes, separate the three layers explicitly: source-table column, public API contract, and visible UI. A source column needed for lookup (for example BigQuery `leader_name`) does not automatically mean the UI should show it, and the API should keep the project’s public naming convention (usually camelCase) rather than exposing both camelCase and snake_case aliases.
8. In the final report, separate changed vs unchanged repos, list modified files, note lint/build status, and distinguish pre-existing warnings from new errors.
8. If doing browser/preview verification, check candidate ports first and choose an unused alternate port with `--strictPort`; after curl/browser verification, stop the tracked preview process so it does not keep occupying the user’s Mac.
9. In the final report, separate changed vs unchanged repos, list modified files, note targeted tests/lint/build status, distinguish pre-existing warnings from new errors, and report any temporary preview port/PID plus whether it was stopped.

## Agent runtime continuation + SSE verification

When implementing or debugging a Hermes-like agent runtime that should continue after tool calls instead of requiring the user to keep prompting:

1. Treat the stop condition as a classifier, not simply “no tool calls means final.” Add explicit states for final, empty, post-tool-empty, post-tool-incomplete, intermediate acknowledgment, needs-tool, truncated, and blocked/safety.
2. Put tool-use enforcement and “finish the job” guidance in the system prompt: if the model says it will read/search/write/run/verify, it must actually call a tool; plans or promises are not final answers for action requests.
3. After a tool result, if the next model response is empty or still sounds like “I will summarize,” inject a synthetic continuation prompt that tells the model to process the tool result and finish the user-visible answer.
4. Preserve provider finish metadata in the normalized model response. In Gemini-style clients, capture candidate `finish_reason` and prompt-feedback block reasons; auto-continue on MAX_TOKENS/LENGTH/INCOMPLETE, and fail explicitly on safety/block reasons.
5. Test continuation behavior with deterministic fake models: one that returns an intermediate acknowledgment before final text, one that calls a tool then returns empty before final text, and one that returns MAX_TOKENS before a continuation. These tests catch most “user has to ask again” regressions.
6. For SSE streams, avoid ambiguous event semantics. If both internal progress and final delivery use an event name like `message.completed`, make the frontend distinguish the final envelope shape (for example the final event includes `status` and `answer`) before updating the assistant bubble or closing the EventSource.
7. In browser verification after editing static single-file frontends, reload with a cache-busting query string or otherwise confirm the loaded script contains the expected new guard. Otherwise you can accidentally validate old JavaScript and chase a false regression.
8. When wrapping Hermes `search_files` for a web agent, normalize `target="files"` calls that omit `pattern` by injecting `pattern="*"`. Hermes can return `{"total_count": 0}` for `target="files"` with only `path`, even when the directory contains files; this looks like a filesystem visibility bug but is actually an argument-shape issue. Add a regression test that creates a temp file and calls the wrapper with no pattern.
9. When debugging Hermes-like `web_search`, separate runtime/tool-loop health from provider quality. Hermes expects `query`/`limit`, but models may emit `q`, `search`, `keyword`, `max_results`, `num_results`, or `count`; normalize these before `handle_function_call`. Trim long provider descriptions before feeding results back to the model, and expose/search-log the active backend because blank Hermes web config may auto-fall back to `ddgs`, whose result quality is usable but less stable. See `references/hermes-like-agent-web-search-debugging.md` for the probe and reporting recipe.

## Local full-stack dev server verification

When the user asks you to run a frontend/backend project locally for browser inspection, especially with Vite + API proxy setups:

See also: `references/local-dev-cors-origin-mismatch.md` for the common localhost vs 127.0.0.1 origin trap that looks like a generic login failure.

1. Read the repo's local-dev notes and Vite/server config first; do not guess ports. Preserve documented port pairings because proxy routes often depend on them.
2. Check whether the required ports are already occupied. If they are occupied by a stale server from another worktree/task, stop that stale process before starting the current project, and report that you did so.
3. If the repo path contains shell metacharacters or spaces that make a direct `workdir` unsafe, use a simple safe `workdir` and `cd` into the repo inside the command.
4. Start backend and frontend from the documented working directories, with the documented environment boundary. If the backend imports a sibling package from the repo root, prefer a scoped `PYTHONPATH`/import-path fix for the launch command rather than editing code just to make startup work.
5. For Vite apps that intentionally use local proxying, keep `VITE_API_BASE_URL=` empty if that is how the project routes `/api` and `/dashboard/api` through Vite; otherwise inject the backend origin explicitly.
6. Verify both layers separately: backend health/API endpoint directly, frontend HTML route, then at least one frontend-proxied API URL through the Vite port. Prefer the feature-specific API path when the task is tied to a dashboard feature (for example, for an opinions/tag-filter task verify `/dashboard/api/v1/opinions/label-definitions` and a small `/dashboard/api/v1/opinions?page=1&limit=2` request through Vite), not only the app shell HTML.
7. If a background server watch pattern catches `Traceback` but the server continues to print `Application startup complete`, do not immediately report the server as failed. Read the process log, identify whether the traceback is a trapped/non-fatal library warning (for example passlib reading bcrypt version metadata), then prove runtime health with direct backend and proxied frontend HTTP checks before deciding whether to restart or debug further.
8. If the documented backend port is already occupied and the frontend proxy is hard-coded to that port, do not edit config just to run locally. Start the backend on a free alternate port, start Vite on a free alternate port with the app's API-origin env var pointing at that backend (for this repo pattern: `VITE_API_BASE_URL=http://127.0.0.1:<backend_port>`), and set backend CORS/env to allow the chosen frontend origin when needed.
8. When using an explicit `VITE_API_BASE_URL` to bypass the Vite proxy, verify the browser origin against backend CORS, not just curl. If development CORS allows `http://localhost:<frontend_port>` but not `http://127.0.0.1:<frontend_port>`, give the user the localhost URL and test login/navigation there; 127.0.0.1 can fail with `Failed to fetch` even while curl and localhost work.
9. If auth blocks the dashboard, use the repo's documented or code-discoverable local development account only after verifying how it is created; do not invent credentials.
10. Report URLs, ports, PIDs/session IDs, verification endpoints/statuses, and any port conflicts resolved, so the user can inspect or stop the servers confidently.

See `references/local-dev-servers.md` for a concise checklist and launch/report pattern.

## Local frontend/backend run verification

When the user asks to run a local web project for browser review, especially when they mention ports or frontend/backend communication:

For Hermes-like web agent wrappers that expose terminal/process tools and need to start servers themselves, also see `references/hermes-like-agent-server-lifecycle.md` for stable task IDs, full `handle_function_call` context, background server startup, `search_files(target="files")` pattern normalization, process registry notification draining, session/run/tool/skill API parity, SSE lifecycle events, context compression, and frontend event-shape pitfalls. For broader Hermes parity work in a FastAPI/SSE web runtime, use `references/hermes-like-agent-web-runtime-parity.md` for dispatch context, session/runs APIs, slash commands, search_files normalization, OpenAI-compatible endpoints, and context compression checks. For dangerous-tool safety parity, approval state machines, and checkpoint/rollback implementation details, use `references/hermes-like-aiagent-approval-checkpoint.md`. For aiagent bugs where memory claims, prior-session recall, or dangerous approval behavior diverges from Hermes, use `references/hermes-like-aiagent-memory-session-approval-debugging.md` for the DB-inspection recipe and Hermes parity notes. For behavior-preserving aiagent module-split refactors, use `references/hermes-like-aiagent-refactor-module-split.md` for route-decorator, import, test-baseline, and live-smoke pitfalls. For approval UI regressions where clicking `允許` executes a tool but does not resume the original agent task or produce a final answer, use `references/hermes-like-aiagent-approval-resume.md`.

1. Inspect the repo's local-dev docs and Vite/Next/proxy config before starting servers.
2. Check whether required ports are already occupied and identify the owning process/worktree before killing anything. Stale dev servers from another worktree can silently serve the wrong code.
3. If the current task folder only contains a frontend but the Vite proxy points at an already-running backend (commonly `127.0.0.1:8000`), do not kill or replace that backend unless it is clearly stale for the same worktree. Identify the backend owner with PID + cwd before interpreting API behavior; a stale server from another task/worktree can produce 404s for endpoints that exist in the current repo. Prefer launching the current backend on a free alternate port first, then compare endpoint responses before deciding whether to replace the process on the documented port.
4. If the repo or task-wrapper path contains spaces, shell metacharacters, or non-ASCII characters that the terminal backend rejects as `workdir`, use a safe `workdir` such as the user's home directory and `cd '<absolute repo path>' && ...` inside the command. Do not retry the rejected `workdir` shape.
5. Prefer launching the frontend on a free alternate port with `--strictPort`, preserve `VITE_API_BASE_URL=` when proxy routing is intended, and verify a proxied backend endpoint through the frontend port. If using an explicit `VITE_API_BASE_URL` that points directly to an alternate backend, verify CORS/login behavior in the browser before handing the URL to the user.
6. Start backend and frontend from the documented working directories, preserving required environment variables (for example an empty `VITE_API_BASE_URL=` when the project expects local proxy routing).
7. Verify both layers with real requests: backend health/API endpoint, frontend page HTML, and a proxied frontend API path that proves frontend-to-backend routing works.
8. If authentication gates the page, use the documented local credentials only when discoverable from the repo, then verify the target page contents in the browser.
9. For dashboard field-display changes backed by API contracts, add an API-level verification that fetches a representative detail record and asserts the intended public API contract exactly: new camelCase fields exist, removed fields are absent, and source-table snake_case names are not leaked as response aliases unless explicitly required. Example: if BigQuery source column is `leader_name`, the API response should expose only `leaderName`; assert `leader_name` and obsolete fields such as `room` are absent before handing over the URL.
10. Report URLs, ports, PIDs/session IDs, and any stale processes that were stopped; if you reused an existing backend, report its PID/worktree and that it was left untouched.

## Verification Checklist

- [ ] Root cause identified, not just a symptom patched
- [ ] Regression test added or updated when behavior changed
- [ ] Relevant verification commands re-run
- [ ] Pre-commit review passed or issues were documented
- [ ] Debugger choice matched the target runtime
- [ ] For mixed-env ETL, exact source and sink routing verified before writes
- [ ] For local full-stack runs, documented ports/proxies verified end-to-end before handing over the browser URL
- [ ] For frontend UI cleanup, obsolete labels/handlers are absent and layout gaps are checked