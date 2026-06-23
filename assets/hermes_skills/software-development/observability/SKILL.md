---
name: observability
description: Instrument services, jobs, and multi-repo rollouts with error capture, tracing, and deployment wiring.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [observability, monitoring, tracing, error-capture, Sentry, rollout]
---

# Observability

Use this skill when adding, changing, or verifying observability across one or more codebases: error capture, traces, release metadata, metrics, and deployment wiring.

This is the umbrella for:
- service-level observability rollout across multiple repos
- Python service instrumentation
- job/worker/CLI tracing and error capture
- safe environment-driven configuration
- verification and rollout hygiene

## Core workflow

1. Identify every runtime boundary
   - browser/frontend
   - backend HTTP service
   - worker/job/CLI
   - scheduler-triggered process
   - deployment config that injects env vars or build args

2. Decide the observability goals
   - unhandled exception capture
   - handled error reporting
   - request / span tracing
   - release/environment tagging
   - performance sampling

3. Keep initialization configuration-driven
   - enable only when the relevant env var is present
   - keep sample rates explicit
   - avoid hidden defaults that differ between code and deployment

4. Wire each runtime correctly
   - frontend: initialize early and propagate trace targets only to intended hosts
   - backend: initialize during app startup and capture exceptions at integration boundaries
   - worker/CLI/job: initialize once at process start, wrap major work units in spans, flush before exit

5. Update the delivery pipeline together
   - code
   - env.example / docs
   - Dockerfile / build config / deploy YAML
   - release notes or deployment checklist if needed

6. Verify before declaring success
   - import / compile / unit tests as appropriate
   - a controlled failure appears in the observability backend
   - traces or spans show up where expected
   - deployment config matches the runtime variable names

## Multi-repo rollout subsection

When observability is being added across more than one repository:
- fetch the latest remote base branch first
- create a dedicated feature branch per repo or a coordinated rollout branch set
- inspect each repo independently; one repo passing does not validate the others
- confirm the deployment file for each runtime, not just the application code
- keep sample rate and DSN/config names identical across code, docs, and deployment unless there is an intentional difference

## Python service subsection

When the target is a Python service:

- initialize from environment variables and skip cleanly when disabled
- add the SDK dependency with a version compatible with the codebase’s Python version
- capture intentionally swallowed exceptions before returning HTTP responses
- avoid noisy capture of routine validation errors unless they signal a real bug
- document the runtime knob(s) in README or deployment docs
- verify with a local run and a controlled exception

### Python-specific patterns

- Flask / WSGI: initialize before serving requests and use framework integration when appropriate
- FastAPI / ASGI: initialize before request handling begins and ensure middleware order is correct
- background workers: initialize once per process and flush on exit

## Job / CLI subsection

For schedulers, batch jobs, and CLIs:
- initialize once at process start
- bracket major units of work with transactions or spans
- flush before exit if the SDK buffers events
- if a job is cron-driven, the prompt or wrapper should make the monitoring condition explicit

## Log-only diagnostic instrumentation

Use this pattern when the goal is to investigate production/API timeout or pressure behavior without changing service semantics.

- Keep the patch intentionally observational: do not change API schema, timeout values, concurrency, retry policy, model calls, or existing public function signatures.
- Add a per-request trace/request ID at the HTTP boundary and carry it through log messages where possible.
- Log request start, finish, duration, route, method, status code, payload size/count metadata, and exception/teardown failures.
- Redact or summarize sensitive fields; never log raw passport images, full OCR payloads, or complete identity data.
- Prefer small helper functions for safe log value conversion so logging failures cannot break the request path.
- For pressure-test preparation, make the resulting logs sufficient to reconstruct: arrival rate, concurrent in-flight behavior, slow external calls, timeout location, and whether failures happened before/inside/after the core recognition service.
- Verify the patch with syntax checks and smoke tests for all touched endpoints before declaring it safe.

## Common pitfalls

- hardcoding DSNs or endpoints in source
- mismatching sample rates between code and deployment config
- shipping instrumentation without the env var wiring that activates it
- confusing deployment failures with upstream latency or timeout problems
- capturing expected validation failures and creating noise
- checking only one repo in a multi-repo rollout
- mixing diagnostic logging with behavior changes; for production pressure investigations, keep log-only patches separate from remediation patches

## Verification checklist

- [ ] Relevant env vars documented
- [ ] Initialization is disabled when config is absent
- [ ] Sample rate is explicit and consistent
- [ ] Deployment files updated together with code
- [ ] Controlled failure is captured
- [ ] Trace/span path verified for the target runtime
- [ ] Each affected repo validated independently

## See also

Use support files under `references/` for rollout notes, framework-specific snippets, and session examples; use `scripts/` for repeatable checks or probes.

Reference files:
- `references/log-only-api-pressure-diagnostics.md` — checklist for API pressure/stability investigations where the first patch must be diagnostic logging only, with no behavior/config/schema changes.
