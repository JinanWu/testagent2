# Log-only API pressure diagnostics

Use when preparing a service for pressure/stability testing and the first step is to add observability without changing behavior.

## Pattern

1. Keep code changes observational only.
   - Do not change API schema, timeout/concurrency settings, retry policy, deployment YAML, or existing public function signatures.
   - Do not combine remediation with instrumentation in the same patch unless the user explicitly asks.

2. Instrument the HTTP boundary.
   - Generate or read a trace/request ID.
   - Log request start and finish.
   - Log teardown/late exceptions separately so failures after response handling are visible.

3. Capture timing and routing context.
   - route, method, status code
   - duration in milliseconds
   - request/content size or batch count where safe
   - high-level stage markers around core service/model calls

4. Protect sensitive data.
   - Redact passport/identity fields.
   - Summarize images and OCR payloads by size/type/count, not raw content.
   - Use safe value conversion helpers so logging cannot raise new request-path errors.

5. Verify before handoff.
   - `git diff --check`
   - Python compile/import checks for touched files
   - smoke tests for each touched endpoint, including health, single-recognition, and batch-recognition routes when present
   - inspect diff to confirm no function-signature, timeout, concurrency, API schema, or deployment changes slipped in

## Handoff format

Report:
- repo path and branch
- modified files and diff stat
- explicit statement that behavior/config/API schema were not changed
- verification commands and pass/fail results
- endpoint smoke-test status codes
