# Cloud Run + Gemini triage notes

This reference captures the practical thresholds and interpretation rules used when debugging Cloud Run services that call Gemini / Vertex AI.

## Cloud Run thresholds
- Request timeout: default 300 seconds; can be extended up to 3600 seconds.
- Pending queue: a request can wait in the pending queue for up to 10 seconds or 3.5x predicted cold-start time, whichever is higher, before Cloud Run tries to start a new on-demand instance.
- Concurrency matters more when the app is CPU-bound or the service is effectively single-threaded.
- `max-instances=1` can concentrate all load on a single hot instance.

## Gemini / Vertex AI thresholds
- Gemini API rate limits are typically measured by:
  - RPM: requests per minute
  - TPM: tokens per minute
  - RPD: requests per day
- Rate limits are evaluated per project and vary by model and tier.
- Common evidence of quota exhaustion:
  - HTTP 429
  - `RESOURCE_EXHAUSTED`
  - `rate limit exceeded`
  - `quota exceeded`
- If a request is slow but still returns 200, and there are no quota errors, the problem is usually latency, retries, queueing, or upstream degradation rather than a hard limit.

## Log pattern to look for
- One batch request may fan out into many upstream `generateContent` calls.
- A long silent gap between successful upstream calls is a stronger clue than the aggregate request duration.
- When a batch endpoint processes multiple images, "60s per image" often means one batch-level request is blocked by one delayed upstream call.

## Practical interpretation used in this project
- Slow 200 responses are treated as a performance problem, not a deployment failure.
- Quota issues are only claimed when a rate-limit signature is visible in logs.
- When latency spikes without quota errors, investigate:
  1. upstream call timing gaps
  2. retry/backoff behavior
  3. fan-out size per request
  4. Cloud Run concurrency and instance caps
