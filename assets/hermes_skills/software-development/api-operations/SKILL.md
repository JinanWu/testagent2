---
name: api-operations
description: Use when inspecting HTTP APIs for schemas, representative samples, or short burst/load tests; keeps provenance, edge-blocking, timeout, and artifact handling explicit.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [api, http, reconnaissance, load-testing, timeout, debugging]
    related_skills: [cronjob-management, hermes-agent-skill-authoring]
---

# API Operations

## Overview

Use this skill for the common class of HTTP API work where you either need to **inspect what an API returns** or **apply controlled pressure to see how it behaves**. Those are separate modes, but they share the same first steps: identify the real endpoint, keep the request shape narrow, preserve provenance, and report results in a way a maintainer can act on.

This skill is intentionally broad. A maintainer should be able to use it for schema reconnaissance, sample extraction, endpoint comparison, burst tests, timeout reproduction, and edge/CDN/WAF troubleshooting without needing a separate one-off skill for each session artifact.

## When to Use

- You need to inspect an API’s fields, example records, or endpoint differences.
- You need a short burst, spike, timeout, or resilience test against an API.
- You need to determine whether a failure happened at DNS, edge/CDN/WAF, origin, or application level.
- You want a concise operational report with minimal raw data but enough evidence to reproduce.

Do not use this skill for large-scale chaos testing or long-running benchmarks unless the user explicitly wants that. Keep the scope narrow and auditable.

## Core Workflow

1. **Identify the exact endpoint and environment.**
   - Confirm host, path, auth, and query/body parameters.
   - Prefer the real pipeline endpoint over a guessed or convenience URL.
   - If the source data is local files or images, confirm the source directory before sending anything to an external API unless the user already authorized it.

2. **Choose the operating mode.**
   - **Reconnaissance**: sample a few rows, derive the field set, compare endpoints.
   - **Burst testing**: validate latency, error mode, timeout behavior, and saturation.

3. **Keep artifacts auditable and small.**
   - Save request metadata and the smallest useful response sample.
   - Avoid dumping large payloads unless explicitly requested.
   - Record the command, params, and run directory so the result can be replayed.

4. **Verify the layer that actually failed.**
   - DNS failure is not backend failure.
   - Edge/WAF challenge is not origin/app behavior.
   - A client timeout is not the same as a 504 or an app-level timeout.
   - If the API expects a signed or date-derived key, derive it from the docs first; do not assume a guessed token is meaningful.

5. **Report concisely.**
   - Give the endpoint, parameters, status, count, field count, latency, and one or two representative examples.
   - State the likely bottleneck and the next check.
   - If the response body is a JSON string that wraps JSON, decode it twice before judging schema or field coverage.

## Signed / dated request workflows

Some travel or vendor APIs accept requests only when a per-day signature/key is supplied.

- Read the docs first for exact header names, required body keys, and signature formula.
- Common pattern: `SHA512(yyyyMMdd + secret)` in uppercase hex.
- When a request is rejected with an auth-like app message, confirm whether it is a missing-client-id issue versus a malformed key issue before changing the payload shape.
- For verification, keep the same date and body constant across 2–3 runs so you can separate backend inconsistency from input variance.
- Prefer a small route/date sample and inspect one representative record that proves the field of interest exists.
- See `references/ai-parser-mpquery-verification.md` for one concrete example and response-decoding notes.
- See `references/ai-parser-mpquery-artifact-capture.md` when the goal is to call AIParserMpQuery and save raw/decoded artifacts for manual website comparison; do not substitute a downstream BigQuery validation for that task.
- See `references/cola-ai-parser-response-capture.md` for a Domanda/Cola flight-response capture recipe: exact AIParserMpQuery payload/auth pattern, raw/decoded artifact layout, flattened return-segment CSV, and airline/time mismatch analysis.
- See `references/domanda-flight-api-raw-response-capture.md` when investigating Domanda flight display mismatches; capture the raw website/API response from the search flow and do not substitute downstream ETL, BigQuery, or Cloud SQL validation.

## API Reconnaissance Subsection

Use this when the user wants fields, schema shape, representative values, or side-by-side comparison of endpoints.

### Workflow

1. Locate the real endpoint or embedded data source.
   - Check code/config for constants, routing, and parameter names.
   - For HTML-backed content, inspect the source or embedded JSON before reaching for browser automation.

2. Query a narrow window.
   - Keep the time range or filter as specific as possible.
   - Use the smallest slice that still answers the question.

3. Sample only a few rows.
   - Inspect 2–3 rows, not the full payload.
   - Derive the union of keys from the sample.
   - Distinguish shared fields, endpoint-specific fields, optional/null fields, and time fields.

4. Flatten nested trees when needed.
   - Traverse recursively and carry parent labels down to the leaves.
   - Treat container nodes as containers unless they clearly represent a product row or record.

5. Verify the result.
   - Report URL, params, status code, record count, and field count.
   - Include one representative record if it helps clarify the schema.

### Output Shape

- Endpoint A: count, field count, shared fields, unique fields
- Endpoint B: count, field count, shared fields, unique fields
- Example values: 2–3 distinct values for the requested field

## Burst / Load Testing Subsection

Use this when the user wants a short load, spike, timeout, resilience, or saturation test.

### Workflow

1. **Confirm scope before sending traffic.**
   - Endpoint/base URL and environment, especially dev/stage/prod.
   - Payload schema and batch sizes/concurrency.
   - Client timeout and maximum runtime/cost cap.
   - If destructive saturation is possible, echo the exact target before launch.

2. **Build an auditable runner.**
   - Write raw response files per request or batch.
   - Save payload metadata, but avoid storing sensitive payload bodies unless needed.
   - If files or images are used, keep a manifest of the exact source paths.

3. **Preflight DNS and edge/CDN/WAF reachability.**
   - Resolve the hostname first.
   - Send a tiny baseline request before interpreting backend behavior.
   - If an edge returns a challenge or 403 page, label the run edge-blocked and do not count it as app behavior.
   - Check origin or app logs for the same time window when the endpoint is behind an edge.

4. **Measure both request-level and item-level outcomes.**
   - HTTP status and latency per request/batch.
   - Item success/failure counts when responses are batch-shaped.
   - Missing item responses when a batch fails early.
   - Client timeout separately from HTTP 408/504 server timeout.
   - Consecutive failure runs and latency degradation across batches.

5. **Validate fixes with the same incident-shaped traffic.**
   - Re-run the smallest traffic pattern that reproduces the issue.
   - Include a `/health` probe and a tiny baseline request before and after.
   - Compare latency buckets, 5xx/504 rates, and handler-entry delay.
   - Separate dispatch starvation from upstream/model or queue saturation when requests enter promptly but item success still collapses.

6. **Report concisely.**
   - Include run directory, summary JSON, report path, manifest path, and raw response directory.
   - Include 2–3 key rows/examples, not full raw dumps.
   - State the likely bottleneck and the next log/config checks.

## Common Pitfalls

1. **Confusing edge failures with backend failures.**
   A fast Cloudflare/WAF 403 or challenge page means the request never reached origin.

2. **Using broad samples for schema work.**
   Large payloads hide the field differences you actually need.

3. **Treating client timeout, gateway timeout, and app timeout as the same thing.**
   They point to different bottlenecks and require different follow-up.

4. **Over-reporting raw data.**
   The goal is a reusable operational summary, not a data dump.

5. **Ignoring provenance.**
   If the payload came from local files, the exact source matters for safety and reproducibility.

## Verification Checklist

- [ ] I identified the exact endpoint, host, and environment.
- [ ] I used the smallest request or sample that answered the question.
- [ ] I separated DNS, edge, origin, and app behavior.
- [ ] I reported status, count, field count, and a representative sample when relevant.
- [ ] I preserved enough artifact metadata to replay the run.
- [ ] I stated the likely bottleneck and the next verification step.

## References

- `references/api-workflow-notes.md` — condensed field-discovery and burst-test patterns, including edge-blocking and provenance reminders.
