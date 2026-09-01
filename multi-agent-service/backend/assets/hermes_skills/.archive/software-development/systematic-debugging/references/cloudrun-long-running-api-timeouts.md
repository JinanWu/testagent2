# Cloud Run long-running API timeouts and proxy 524 investigations

Use this pattern when a partner reports `Timeout`, `remote server error (524)`, or similar gateway/proxy failures for a Cloud Run service, especially when Cloud Run request logs show mostly HTTP 200.

## Key distinction

`524` is typically produced by an outer proxy such as Cloudflare, not by Cloud Run. It means the proxy connected to the origin but did not receive a response before its read timeout. Cloud Run may still finish later and log HTTP 200, so do not equate partner-facing 524s with Cloud Run 5xx counts.

## Evidence to collect

1. From partner/error spreadsheet:
   - total attempts, successes, failures
   - counts for `Timeout`, `524`, `500`, and application-specific failures
   - percentage of failures caused by timeout/proxy classes
2. From Cloud Run request logs:
   - total requests by endpoint
   - status counts
   - latency distribution: p50, p90, p95, p99, max
   - thresholds matching proxy/client limits: >60s, >90s, >100s, >120s
   - request size buckets vs latency and timeout thresholds
   - hourly/day-level concentration of slow requests
3. From stderr/app logs:
   - stack traces and exact exception classes
   - warning patterns that add synchronous work, retries, or per-item reprocessing
   - cold starts/deploy rollouts, but treat them as secondary unless they align with latency spikes
4. From code/deploy config:
   - Cloud Run max instances, concurrency, CPU/memory, workers, timeout
   - internal thread pools/semaphores
   - per-request fan-out count, e.g. N images × M model/API calls
   - synchronous calls inside async request paths

## Interpretation heuristics

- If Cloud Run 5xx is low but partner `Timeout`/`524` is high, suspect outer proxy/client timeout caused by long-running origin requests.
- If latencies cluster near a round number such as 100-125s, suspect a proxy/client timeout boundary.
- If request size strongly correlates with latency, recommend limiting batch size and image/payload size before chasing network errors.
- If an async endpoint calls blocking synchronous functions during retries or post-processing, treat it as a likely long-tail latency amplifier.
- Cloud Run service timeout (for example 900s) does not protect callers if an outer proxy/client times out earlier.

## Common root causes

- Synchronous HTTP endpoint performing too much work per request.
- Batch API accepting large payloads or many images/documents at once.
- Per-item fan-out to multiple model/API calls.
- Single Cloud Run instance with high request concurrency and limited CPU/worker count.
- Blocking retries or fallback recognition inside an async handler.
- Outer proxy such as Cloudflare imposing a lower read timeout than Cloud Run.

## Recommended mitigation order

1. Immediate operational mitigation:
   - reduce batch size and payload/image size
   - avoid >10MB requests; treat very large requests as high timeout risk
   - lower Cloud Run concurrency and allow horizontal scaling
   - increase CPU/memory if CPU-bound or event-loop starvation is plausible
   - verify whether the domain is behind Cloudflare/proxy and whether bypassing or increasing read timeout is possible
2. Application behavior:
   - enforce an application-level timeout below the proxy/client timeout and return a machine-readable error code
   - catch model/API transport errors explicitly and convert to per-item failures
   - use partial-failure semantics for batch endpoints instead of fail-fast `gather`
   - avoid blocking synchronous calls in async handlers; run them in an executor or use async clients
3. Architecture:
   - for real batch workloads, prefer submit-job -> job_id -> poll/callback over a long synchronous HTTP request.

## Report shape

When reporting, separate:

- partner-facing symptoms (`Timeout`, `524`, `500` counts)
- Cloud Run observed behavior (status counts and latency distribution)
- code/config root causes
- immediate mitigation vs durable architectural fix

This prevents over-focusing on rare SSL/API exceptions when the dominant failure mode is long-tail latency crossing a proxy timeout.