---
name: systematic-debugging
description: "4-phase root cause debugging: understand bugs before fixing."
version: 1.1.0
author: Hermes Agent (adapted from obra/superpowers)
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [debugging, troubleshooting, problem-solving, root-cause, investigation]
    related_skills: [test-driven-development, writing-plans, subagent-driven-development]
---

# Systematic Debugging

## Overview

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

**Violating the letter of this process is violating the spirit of debugging.**

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue:
- Test failures
- Bugs in production
- Unexpected behavior
- Performance problems
- Build failures
- Integration issues

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

**Don't skip when:**
- Issue seems simple (simple bugs have root causes too)
- You're in a hurry (rushing guarantees rework)
- Someone wants it fixed NOW (systematic is faster than thrashing)

## The Four Phases

You MUST complete each phase before proceeding to the next.

---

## Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

### 1. Read Error Messages Carefully

- Don't skip past errors or warnings
- They often contain the exact solution
- Read stack traces completely
- Note line numbers, file paths, error codes

**Action:** Use `read_file` on the relevant source files. Use `search_files` to find the error string in the codebase.

### 2. Reproduce Consistently

- Can you trigger it reliably?
- What are the exact steps?
- Does it happen every time?
- If not reproducible → gather more data, don't guess

**Action:** Use the `terminal` tool to run the failing test or trigger the bug:

```bash
# Run specific failing test
pytest tests/test_module.py::test_name -v

# Run with verbose output
pytest tests/test_module.py -v --tb=long
```

### 3. Check Recent Changes

- What changed that could cause this?
- Git diff, recent commits
- New dependencies, config changes

**Action:**

```bash
# Recent commits
git log --oneline -10

# Uncommitted changes
git diff

# Changes in specific file
git log -p --follow src/problematic_file.py | head -100
```

### 4. Gather Evidence in Multi-Component Systems

**WHEN system has multiple components (API → service → database, CI → build → deploy):**

**BEFORE proposing fixes, add diagnostic instrumentation:**

For EACH component boundary:
- Log what data enters the component
- Log what data exits the component
- Verify environment/config propagation
- Check state at each layer

Run once to gather evidence showing WHERE it breaks.
THEN analyze evidence to identify the failing component.
THEN investigate that specific component.

### 5. Trace Data Flow

**WHEN a deployed analytics/dashboard app shows surprising data:**

- Reproduce the exact deployed API response that feeds the UI, and record `source`, `run_id`, `run_ts`, date range, row counts, and metric fields.
- Confirm the serving environment explicitly: Cloud Run service URLs/images/env vars, backend origin, and BigQuery project/dataset. Always pass explicit `--project` when dev/prod both exist.
- Compare raw/source tables against reporting/snapshot tables by period: raw rows, scored/non-null rows, snapshot `opinion_count`/`scored_count`, and metric values.
- Inspect snapshot selection logic: "latest non-empty" can still select mock/simulated snapshots if they have positive counts.
- Check scheduler/job logs for date ranges, upstream API row counts, BigQuery row counts, and whether zero-row jobs still write snapshots.
- See `references/gcp-dashboard-dataflow.md` for a concrete Cloud Run + BigQuery dashboard checklist and common root causes.
- See `references/cloudrun-dashboard-verification.md` for the compact Cloud Run + dashboard mismatch verification pattern: confirm active gcloud identity/project, inspect Cloud Run Ready/traffic/env, fetch the real API path from the JS bundle when needed, and compare API source metadata against the latest BigQuery snapshot.
- See `references/dashboard-metrics-null-root.md` for the concrete pattern where `summary_tree` is present but `metrics_tree` root metrics are null / `scored_count=0`, causing the latest snapshot to be ignored or a prior valid snapshot to be served.
- See `references/gcp-dashboard-snapshot-mismatch.md` for a concrete case where the latest BigQuery snapshot existed but was not serving-ready (`scored_count=0`), causing the API to keep returning the last valid simulated month.
- See `references/hierarchy-drilldown-debugging.md` for the compact drill-down checklist: count branch-vs-leaf nodes, verify the selected path is actually terminal before blaming field names, and compare identifiers to API keys.
- See `references/hierarchy-drilldown-debugging.md` for the compact drill-down checklist: count branch-vs-leaf nodes, verify the selected path is actually terminal before blaming field names, and compare identifiers to API keys.
- See `references/dashboard-hierarchy-monthly-snapshot.md` for the monthly snapshot probe pattern: fetch the real hierarchy JSON with a browser-like User-Agent, save it first, then split the month by top-level category and inspect representative branches/leaves with source metadata.
- See `references/passenger-survey-sparse-tree-backfill.md` for the sparse-tree backfill pattern: verify whether the hierarchy is intentionally sparse, and if the requirement is to make drill-down "normal", move the fix into the ETL/write path and synthesize only the missing structure from flat rows plus a stable key such as `tour_code`.
- For tree-backed dashboards, inspect the upstream snapshot shape first: if BigQuery stores `metrics_tree` / `summary_tree` JSON, a missing `rowdata` column is not a bug by itself. Some branches are intentionally sparse, and the right fix may be to reconstruct missing intermediate nodes only when the leaf already has flat opinions to group (for example by `tour_code`).
- When users ask to "fix the tree" or "write it upstream", do not stop at API read-time augmentation. Verify whether the stored snapshot itself must be rewritten so future consumers see the corrected hierarchy.
- See `references/frontend-ui-verification.md` for a compact checklist for dashboard scrollbar issues and transparent globe/canvas rendering: inspect the full ancestor chain, confirm height constraints, and verify the actual paint source.
- See `references/text-truncation-root-cause-checklist.md` when text looks clipped or ends mid-sentence: compare raw payload vs rendered text, search for hard substring logic like `slice(0, N)`, and confirm the expand/detail view is not reusing the truncated preview variable.
- See `references/dashboard-summary-scroll-reset.md` when a longer summary is replaced by a shorter one but the UI only updates after hover/scroll interaction: the text may be correct while the scroll container preserves stale `scrollTop`.
- See `references/local-vite-api-origin-debugging.md` when a local Vite/React frontend login/API call fails with `Load failed` or similar while the local backend health check is green: inspect the compiled `import.meta.env` in the served module, because `.env.local` can force an absolute remote `VITE_API_BASE_URL` that bypasses the local Vite proxy.
- See `references/dashboard-local-cache-and-date-filter-verification.md` when validating dashboard route/tab switching, API cache behavior, explicit refresh controls, and opinion-search date filters against a real local Vite + FastAPI + BigQuery stack; it includes the fetch-wrapper method for proving “switch away/back does not refetch.”
- See `references/dashboard-opinion-infinite-scroll-mock-verification.md` when checking the opinion-search page's infinite scroll without live BigQuery/gcloud access: use a temporary local mock API on the Vite proxy path, verify page=1/2/3 resource requests and appended article counts, and distinguish React StrictMode duplicate page=1 from a real pagination bug.
- See `references/dashboard-ui-fallback-data.md` for the pattern where a dashboard appears non-clickable because interaction is gated on live data while the rendered fallback data is empty; supply a minimal nested fallback so the UI can be exercised offline.
- See `references/dashboard-tab-cache-and-opinion-date-filter.md` when a React/Vite dashboard remounts an expensive analysis tab after switching pages, or when an opinion-search page needs `YYYY-MM-DD` date query params/response fields while BigQuery is unavailable: cache at the API-client boundary with TTL + in-flight de-dupe + manual refresh, and unit-test SQL/query shaping without live DB access.
- See `references/vite-local-api-origin-debugging.md` when a local Vite/React frontend says `Load failed` even though the local backend/proxy works: inspect the transformed served module for `import.meta.env.VITE_API_BASE_URL`, because `.env.local`/`.env.development` can make browser requests bypass the Vite proxy and hit a remote Cloud Run/API origin.
- See `references/passenger-survey-dashboard-local-feature-verification.md` when verifying 心情指數儀表板 UI/API changes locally: run backend + frontend against dev BigQuery, prefer `localhost:5173` for CORS, do not deploy unless explicitly authorized, measure `/dashboard/api/v1/satisfaction/hierarchy` first-load latency/bytes, and verify manual refresh still works.
- See `references/dashboard-lazy-trend-loading.md` when the 心情指數儀表板 trend chart, top KPI MoM/YoY, or drill-card `較上月` deltas are empty after the hierarchy endpoint was optimized: keep hierarchy fast, lazy-load trend by node, use query params for node IDs with `/`, derive deltas for top KPI and visible child cards, and preserve neutral deltas for missing/gray scores.
- See `references/dashboard-lazy-trend-loading.md` when the optimized fast hierarchy response leaves the right-side trend chart empty or KPI `月/年` deltas as `—`: keep fast first paint, lazy-load `/satisfaction/trend`, use query params for slash-containing node ids, show a chart-scoped loader, and derive MoM/YoY from the returned monthly series.
- See `references/dashboard-lazy-trend-loading.md` when the right-side trend chart card renders but has no lines after the hierarchy API was optimized for speed: keep fast hierarchy, lazy-load a dedicated trend endpoint, use query params for node ids containing `/`, add in-flight de-dupe/loading/error UI, and verify SVG paths/circles in the browser.
- See `references/dashboard-fast-hierarchy-trend-regression.md` when the 心情指數趨勢分析 card shows title/tabs/legend but no line: compare the fast `/satisfaction/hierarchy` payload against `/satisfaction/nodes/{node_type}/{node_id}/trend` before blaming BigQuery or the chart renderer; fast hierarchy may intentionally contain empty trends.
- See `references/passenger-survey-dashboard-backfill.md` for the passenger-survey dashboard backfill/rerun checklist: separate source API env from target BigQuery env, probe `ai-label`/`label-analyze` counts before writes, clean stale/simulated snapshots separately from raw rows, and avoid historical summary reruns when only the latest month is visible.
- See `references/passenger-survey-dashboard-backfill.md` for the passenger-survey dashboard backfill/rerun checklist: separate source API env from target BigQuery env, probe `ai-label`/`label-analyze` counts before writes, clean stale/simulated snapshots separately from raw rows, and avoid historical summary reruns when only the latest month is visible.
- See `references/passenger-survey-dashboard-verification.md` for the post-rerun verification probes: source metadata, 2026 monthly row counts, and tree drill-down JSON checks.
- See `references/passenger-survey-stage3-verification.md` for the Stage 3 dashboard snapshot pattern: monthly BigQuery snapshot, metrics_tree vs summary_tree, and how to interpret empty root summaries.
- See `references/cloudrun-long-running-api-timeouts.md` when a partner reports `Timeout`, `remote server error (524)`, or gateway/proxy failures for a Cloud Run API: compare partner-facing failures against Cloud Run request latency/status logs, request-size buckets, proxy timeout boundaries, and code-level fan-out/blocking work before blaming rare 5xx exceptions.
- See `references/passport-recog-data-timeout-logging-boundaries.md` for the concrete trace-id logging pattern used to isolate where a passport-recognition request spends time (request → batch chunk → image → service → field).
- See `references/passport-recog-data-cloudrun-slow-batch-gap.md` for the Cloud Run batch-slowdown pattern where a request still returns 200 but one upstream model-call window stalls for minutes; use the internal call cadence and silent gap length to distinguish dependency stalls from deployment problems.
- See `references/passport-recog-data-stuck-instance-diagnostic-logging.md` for the stuck-instance case where Cloud Run request logs continue but app logs stop before handler/batch markers; instrument request dispatch, handler entry, batch/semaphore/image workers, service, Gemini, and executor boundaries with `passport_diag event=... trace_id=...` INFO logs.

**WHEN error is deep in the call stack:**

- Where does the bad value originate?
- What called this function with the bad value?
- Keep tracing upstream until you find the source
- Fix at the source, not at the symptom

**WHEN a user describes an empty/failed recognition result:**

- Identify the unit of failure before choosing a retry scope: whole request, one document/image, one extracted field, or one parsed value.
- Preserve latency/cost constraints by retrying only the failed unit when that is what the user requested.
- Add assertions that prove non-empty units were not retried.

**WHEN a user asks for batch/API partial-failure behavior:**

- Treat the batch item (image/document/record) as the isolation boundary unless the user explicitly asks for fail-fast behavior.
- Write a regression test with mixed success and failure items before changing production code.
- Make per-item workers catch expected domain errors and unexpected exceptions so `asyncio.gather()` or equivalent does not abort the whole batch on one item.
- Return machine-readable per-item `error_code` plus human-readable `error`, and keep the HTTP-level success semantics documented separately from per-item success.
- Update API docs with the response contract and error-code table so downstream teams know how to handle retries/manual review.
- See `references/batch-partial-failure-api-contract.md` for a compact example pattern.
- See `references/hierarchy-drilldown-debugging.md` for a concrete checklist when a dashboard/tree UI can drill down to an intermediate level but appears to stop early.

**Action:** Use `search_files` to trace references:

```python
# Find where the function is called
search_files("function_name(", path="src/", file_glob="*.py")

# Find where the variable is set
search_files("variable_name\\s*=", path="src/", file_glob="*.py")
```

### Phase 1 Completion Checklist

- [ ] Error messages fully read and understood
- [ ] Issue reproduced consistently
- [ ] Recent changes identified and reviewed
- [ ] Evidence gathered (logs, state, data flow)
- [ ] Problem isolated to specific component/code
- [ ] Root cause hypothesis formed

**STOP:** Do not proceed to Phase 2 until you understand WHY it's happening.

---

## Phase 2: Pattern Analysis

**Find the pattern before fixing:**

### 1. Find Working Examples

- Locate similar working code in the same codebase
- What works that's similar to what's broken?

**Action:** Use `search_files` to find comparable patterns:

```python
search_files("similar_pattern", path="src/", file_glob="*.py")
```

### 2. Compare Against References

- If implementing a pattern, read the reference implementation COMPLETELY
- Don't skim — read every line
- Understand the pattern fully before applying

### 3. Identify Differences

- What's different between working and broken?
- List every difference, however small
- Don't assume "that can't matter"

### 4. Understand Dependencies

- What other components does this need?
- What settings, config, environment?
- What assumptions does it make?

---

## Phase 3: Hypothesis and Testing

**Scientific method:**

### 1. Form a Single Hypothesis

- State clearly: "I think X is the root cause because Y"
- Write it down
- Be specific, not vague

### 2. Test Minimally

- Make the SMALLEST possible change to test the hypothesis
- One variable at a time
- Don't fix multiple things at once

### 3. Verify Before Continuing

- Did it work? → Phase 4
- Didn't work? → Form NEW hypothesis
- DON'T add more fixes on top

### 3.5. Make test discovery local in nested repos

When pytest lives inside a sub-repo of a larger workspace, make the target repo self-contained for test collection before debugging the code itself:
- Pin discovery to the repo with `--rootdir=.` when pytest is wandering into sibling projects.
- If imports fail during collection, add a minimal `tests/conftest.py` that inserts the repo root into `sys.path` before importing the package under test.
- Re-run one focused test file first to confirm collection is isolated, then expand outward.

See `references/monorepo-pytest-rootdir.md` for the compact recipe.

### 4. When You Don't Know

- Say "I don't understand X"
- Don't pretend to know
- Ask the user for help
- Research more

---

## Phase 4: Implementation

**Fix the root cause, not the symptom:**

### 1. Create Failing Test Case

- Simplest possible reproduction
- Automated test if possible
- MUST have before fixing
- Use the `test-driven-development` skill

### 2. Implement Single Fix

- Address the root cause identified
- ONE change at a time
- No "while I'm here" improvements
- No bundled refactoring

### 3. Verify Fix

```bash
# Run the specific regression test
pytest tests/test_module.py::test_regression -v

# Run full suite — no regressions
pytest tests/ -q
```

### 4. If Fix Doesn't Work — The Rule of Three

- **STOP.**
- Count: How many fixes have you tried?
- If < 3: Return to Phase 1, re-analyze with new information
- **If ≥ 3: STOP and question the architecture (step 5 below)**
- DON'T attempt Fix #4 without architectural discussion

### 5. If 3+ Fixes Failed: Question Architecture

**Pattern indicating an architectural problem:**
- Each fix reveals new shared state/coupling in a different place
- Fixes require "massive refactoring" to implement
- Each fix creates new symptoms elsewhere

**STOP and question fundamentals:**
- Is this pattern fundamentally sound?
- Are we "sticking with it through sheer inertia"?
- Should we refactor the architecture vs. continue fixing symptoms?

**Discuss with the user before attempting more fixes.**

This is NOT a failed hypothesis — this is a wrong architecture.

---

## Red Flags — STOP and Follow Process

If you catch yourself thinking:
- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "Add multiple changes, run tests"
- "Skip the test, I'll manually verify"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- "Pattern says X but I'll adapt it differently"
- "Here are the main problems: [lists fixes without investigation]"
- Proposing solutions before tracing data flow
- **"One more fix attempt" (when already tried 2+)**
- **Each fix reveals a new problem in a different place**

**ALL of these mean: STOP. Return to Phase 1.**

**If 3+ fixes failed:** Question the architecture (Phase 4 step 5).

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Issue is simple, don't need process" | Simple issues have root causes too. Process is fast for simple bugs. |
| "Emergency, no time for process" | Systematic debugging is FASTER than guess-and-check thrashing. |
| "Just try this first, then investigate" | First fix sets the pattern. Do it right from the start. |
| "I'll write test after confirming fix works" | Untested fixes don't stick. Test first proves it. |
| "Multiple fixes at once saves time" | Can't isolate what worked. Causes new bugs. |
| "Reference too long, I'll adapt the pattern" | Partial understanding guarantees bugs. Read it completely. |
| "I see the problem, let me fix it" | Seeing symptoms ≠ understanding root cause. |
| "One more fix attempt" (after 2+ failures) | 3+ failures = architectural problem. Question the pattern, don't fix again. |

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence, trace data flow | Understand WHAT and WHY |
| **2. Pattern** | Find working examples, compare, identify differences | Know what's different |
| **3. Hypothesis** | Form theory, test minimally, one variable at a time | Confirmed or new hypothesis |
| **4. Implementation** | Create regression test, fix root cause, verify | Bug resolved, all tests pass |

## Hermes Agent Integration

### Investigation Tools

Use these Hermes tools during Phase 1:

- **`search_files`** — Find error strings, trace function calls, locate patterns
- **`read_file`** — Read source code with line numbers for precise analysis
- **`terminal`** — Run tests, check git history, reproduce bugs
- **`web_search`/`web_extract`** — Research error messages, library docs

### With delegate_task

For complex multi-component debugging, dispatch investigation subagents:

```python
delegate_task(
    goal="Investigate why [specific test/behavior] fails",
    context="""
    Follow systematic-debugging skill:
    1. Read the error message carefully
    2. Reproduce the issue
    3. Trace the data flow to find root cause
    4. Report findings — do NOT fix yet

    Error: [paste full error]
    File: [path to failing code]
    Test command: [exact command]
    """,
    toolsets=['terminal', 'file']
)
```

### With test-driven-development

When fixing bugs:
1. Write a test that reproduces the bug (RED)
2. Debug systematically to find root cause
3. Fix the root cause (GREEN)
4. The test proves the fix and prevents regression

## Real-World Impact

From debugging sessions:
- Systematic approach: 15-30 minutes to fix
- Random fixes approach: 2-3 hours of thrashing
- First-time fix rate: 95% vs 40%
- New bugs introduced: Near zero vs common

**No shortcuts. No guessing. Systematic always wins.**
