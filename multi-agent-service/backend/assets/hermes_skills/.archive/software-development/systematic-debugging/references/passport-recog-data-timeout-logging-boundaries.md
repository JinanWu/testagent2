# Passport-recog-data timeout logging boundaries

Session-proven logging pattern for diagnosing Cloud Run / upstream timeout issues in passport recognition flows.

What to log, in order:

1. HTTP request boundary (`app.py`)
- Generate/propagate a `trace_id` from `X-Request-Id` or `X-Cloud-Trace-Context`; fallback to a UUID.
- Log request start and completion in `before_request` / `after_request`.
- Include: `trace_id`, path, method, status, elapsed seconds.
- For batch requests, log total image count, total base64 chars, and a small sample of IDs.

2. Batch orchestration boundary
- Log batch start with `trace_id`, `total_images`, `IMAGE_CONCURRENCY`, and `BATCH_SIZE`.
- For each chunk, log chunk start/done, chunk index, chunk size, sample IDs, and chunk elapsed time.
- Log each image worker start/done with `trace_id`, `image_id`, base64 length, elapsed time, and success/failure.

3. Service boundary (`passport_service.py`)
- Log base64 decode size before/after decoding.
- Log empty-field detection and per-field retry attempts.
- Log final service completion with total elapsed and number of empty-field retries.

4. Gemini field boundary (`vision_analyzer.py`)
- Log field start/done/failure with `trace_id`, `image_id`, field name, image byte size, and elapsed time.
- Keep logs at INFO for timing and WARNING for failures.
- Avoid logging raw image/base64 content or secrets.

Operational notes:
- Keep `LOG_LEVEL` configurable; INFO is usually needed in Cloud Run for this investigation.
- The useful debugging signal is the first boundary where latency spikes or logs stop appearing.
- If request-level completion is missing but image/field logs exist, suspect outer gateway/proxy timeout.
- If image logs appear but field logs stop, suspect Gemini/API/network latency or per-field fan-out.
- If field logs exist but service completion is missing, suspect parsing or empty-field retry amplification.

This pattern is useful when upstream reports `timeout`, `524`, or generic gateway errors while Cloud Run still shows mostly 200s.
