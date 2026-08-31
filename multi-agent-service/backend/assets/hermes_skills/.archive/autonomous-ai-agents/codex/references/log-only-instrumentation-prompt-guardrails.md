# Log-only instrumentation prompt guardrails

Use this when delegating a logging/instrumentation-only task to Codex.

## Prompt constraints

Tell Codex explicitly:
- The task is observation only: add INFO-level logs, not behavior changes.
- Do not change public or internal function signatures unless the user explicitly asks.
- Do not rewrite control flow, retry behavior, timeout values, concurrency settings, API schemas, response semantics, deployment config, or cloud settings.
- Do not wrap large existing sections in new broad `try/except` blocks just to log.
- Prefer inserting `logger.info(...)` immediately before/after existing operations.
- If correlation is needed, prefer a small request-boundary helper/context in the outer layer; avoid threading new parameters through the whole call graph unless necessary and approved.
- Preserve existing dirty/uncommitted changes unrelated to the task.

## Verification after Codex

Run and inspect:

```bash
git status --short --branch
git diff --stat
git diff --check
git diff -- app.py src/passport_service.py src/vision_analyzer.py | grep -E '^[-+]def |^[-+]async def |^[-+]    def |^[-+]    async def ' || true
python3 -m compileall app.py src
```

Review targeted diffs for:
- New or changed function signatures.
- Reordered retry or timeout logic.
- New broad exception handlers.
- Modified deployment/config files.
- Logs that include sensitive payloads such as raw image/base64/passport content.

If Codex over-instruments:
1. Restore affected files from `HEAD` or the pre-task snapshot, while preserving user-owned dirty changes.
2. Reapply only the minimal log statements.
3. Re-run syntax/smoke checks.

## Useful log-only pattern

Good instrumentation is boundary-based:
- request start/finish
- handler entry
- batch/chunk start/finish
- semaphore wait/acquired
- worker start/finish/exception
- service decode/analyzer/parser/retry start/finish
- executor submit/result
- upstream model call start/finish/failure

Use safe metadata only: trace/request id, elapsed seconds, counts, byte lengths, field names, exception type, configured worker/concurrency counts.
