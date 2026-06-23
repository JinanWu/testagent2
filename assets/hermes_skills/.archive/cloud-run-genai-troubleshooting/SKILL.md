---
name: cloud-run-genai-troubleshooting
description: Triage latency, timeout, quota, and deployment issues for Cloud Run services that call Gemini/Vertex AI or other GenAI backends.
---

# Cloud Run + GenAI troubleshooting

Use this skill when a Cloud Run service is slow, intermittently failing, or looks like it is "stuck deploying", especially when the service fans out to Gemini / Vertex AI / other upstream APIs.

## What to check first
1. Confirm whether the service is actually deployed and ready, or still rolling out.
2. Separate three failure classes:
   - deployment / rollout state
   - Cloud Run serving limits (timeout, concurrency, instance count)
   - upstream GenAI quota / latency / retry behavior
3. Inspect logs around the slow request window, not just aggregate counts.

## Cloud Run signals that matter
- `Ready=True`, `latestReadyRevisionName`, and `traffic` tell you whether rollout finished.
- A request that returns 200 but takes a long time is usually a serving/pathology issue, not a deploy failure.
- Compare request latency with the service timeout. If latency is well below the timeout, look for internal fan-out or upstream waiting.
- Concurrency and instance limits can amplify latency:
  - high concurrency on CPU-bound or retry-heavy code can create queueing inside one instance
  - `max-instances=1` can force all traffic through a single hot instance
  - long requests may wait for an open slot before a new instance is started

## GenAI / Gemini signals that matter
- Look for evidence of quota errors before assuming a quota issue:
  - HTTP 429
  - `RESOURCE_EXHAUSTED`
  - `rate limit exceeded`
  - `quota exceeded`
- If you only see long gaps between successful `generateContent` calls, suspect upstream slowness, retry backoff, or internal fan-out saturation rather than quota rejection.
- Differentiate interactive calls from batch APIs. Their quotas and limits are not the same.
- A useful failure signature is: request log shows 200, stderr shows repeated `429 RESOURCE_EXHAUSTED` from Gemini, and the app logs empty-field rechecks or retry fan-out. That usually means upstream pressure plus internal amplification, not a Cloud Run crash.

## Log analysis pattern
1. Find the slow request(s) and note:
   - start timestamp
   - end timestamp
   - request URL
   - latency/status
2. Check both request logs and application stderr for the same window.
   - Cloud Run request logs can be 200 while stderr already shows upstream failures or retries.
   - For Gemini/Vertex AI issues, search for `RESOURCE_EXHAUSTED`, `429`, `INVALID_ARGUMENT`, and SDK/client errors in stderr.
3. Isolate the same instance / revision around that time.
4. Count upstream calls made during the request window.
5. Look for long silent gaps between upstream calls; these are usually the best clue.
6. Compare "one good request" vs "one bad request" in the same revision to identify whether the issue is data-dependent.

## Cloud Run log queries that are worth running
- `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="<service>" AND logName="projects/<project>/logs/run.googleapis.com%2Frequests" ...'`
- `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="<service>" AND logName="projects/<project>/logs/run.googleapis.com%2Fstderr" ...'`
- Confirm rollout state with `Ready=True`, `latestReadyRevisionName`, and `traffic` before assuming a runtime issue.


## Interpretation rules
- 200 + very slow latency = request completed, but internal or upstream work was slow.
- 500/503 with stack traces around upstream calls = code path or retry failure.
- No quota errors + long gaps between successful GenAI calls = not a hard quota ceiling; likely contention, backoff, or upstream degradation.
- A batch endpoint that processes multiple images in one HTTP request can appear to have "60s per image" even when the real issue is a single delayed upstream call inside the batch.

## Recommended investigation order
1. Cloud Run readiness / revision / traffic split
2. Request latency distribution for the affected time window
3. Upstream call count and timing gaps
4. Presence or absence of quota / 429 / RESOURCE_EXHAUSTED errors
5. Service config: timeout, concurrency, max instances, min instances
6. Code paths that fan out requests or use retries
7. If billing export is missing, estimate spend from logs before guessing: request count -> batch image count -> model calls per image -> token pricing -> retry premium

## Cost estimation from logs
When you need a quick Vertex AI spend estimate but do not yet have Cloud Billing Export:
- Use batch-log image counts instead of raw request count when one HTTP request fans out over multiple images.
- Count the number of model calls per image from the code path; per-field fan-out is often the true multiplier.
- Apply the public model token pricing to an approximate input/output token budget.
- Add a retry premium when logs show `429 RESOURCE_EXHAUSTED`, empty-field rechecks, or repeated retries.
- Treat the result as a range, not a single exact number, until billing export is available.

## Pitfalls
- Do not infer "quota problem" from slowness alone.
- Do not treat a successful 200 as evidence the service is healthy if latency is extreme.
- Do not compare only average latency; one outlier request can reveal the real bottleneck.
- Do not stop at Cloud Run settings: the upstream GenAI call pattern is often the true bottleneck.
- Do not estimate spend from request count alone when batch size varies materially.

## Support files
- `references/cloud-run-genai-quota-and-timeout-notes.md`
- `references/prod-cloudrun-log-triage-2026-05-28.md`
- `references/prod-vertexai-cost-estimation-from-cloudrun-logs.md`
- `scripts/parse-cloudrun-batch-window.py`
