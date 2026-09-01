# Passport-recog-data stuck-instance diagnostic logging

Use this when passport-recog-data / Cloud Run shows request logs but the app appears stuck: `/health` times out, small 1–3 image requests time out, and app logs stop before `開始批次辨識` or handler-level markers.

## Reproduction pattern that motivated this logging

Observed failure was not a single huge batch. The closer reproduction was:
- 15 requests in ~15 seconds
- 1 request/sec
- 2–4 images per request
- maxScale=1, containerConcurrency=80
- app stack: Flask async + WsgiToAsgi + Hypercorn
- internal limits: IMAGE_CONCURRENCY=5, BATCH_SIZE=100, GEMINI_MAX_WORKERS=40, GEMINI_RETRY_WORKERS=8

Symptoms:
- Early requests return but latency increases.
- Later requests hit client timeout.
- Cloud Run request logs may still show status 200 / small fixed response size.
- App logs stop before handler or batch markers.
- `/health` also times out, proving it is not just a slow image/model call.

## Instrumentation goal

Add INFO-level start/finish/exception logs at each boundary so the next broken run identifies the first missing marker. Do not change behavior, timeout values, concurrency settings, API schemas, or Cloud Run config.

Use a grep-friendly one-line format:

```text
passport_diag event=<event_name> trace_id=<id> key=value ...
```

Generate/propagate `trace_id` from:
1. `X-Request-Id`
2. trace portion of `X-Cloud-Trace-Context`
3. fallback UUID

Avoid raw base64/image/passport content. Safe metadata: ids/indexes, base64 length, decoded byte size, counts, field name, phase, elapsed seconds, exception type, configured worker/concurrency counts.

Round `elapsed_sec` to 2 decimals. Keep samples small: at most first 3 ids/indexes.

## Boundary checklist

### HTTP / Flask boundary (`app.py`)

Events:
- `request_start`: trace_id, method, path, content_length, remote_addr or short UA
- `request_finish`: trace_id, method, path, status_code, elapsed_sec, content_length, response_size
- `request_teardown_exception` or equivalent exception marker: trace_id, path, elapsed_sec, exception_type

This must include `/health`; if Cloud Run logs a request but `request_start` is absent, suspect the request never reached Flask dispatch or the WSGI/ASGI/server layer is stuck.

### Single recognition handler

Events:
- `recognize_request_received`: trace_id, has_image, base64_len
- `recognize_service_start`
- `recognize_service_finish`: elapsed_sec, success/error
- `recognize_timeout` / `recognize_exception`: elapsed_sec, exception_type

### Batch handler / orchestration

Events:
- `batch_request_received`: total_images, total_base64_chars, sample_ids
- `batch_start`: total_images, IMAGE_CONCURRENCY, BATCH_SIZE, timeout_sec
- `chunk_start` / `chunk_finish`: chunk_index, chunk_size, elapsed_sec, success_count, fail_count
- `batch_finish` / `batch_timeout` / `batch_exception`: total elapsed and counts

### Semaphore / image worker

Events:
- `semaphore_wait_start`
- `semaphore_acquired`: elapsed_sec spent waiting
- `image_worker_start`: image_id/index, base64_len
- `image_worker_finish`: elapsed_sec, success, error_code
- `image_worker_exception`: elapsed_sec, exception_type, error_code if known

Interpretation:
- `semaphore_wait_start` without `semaphore_acquired`: app-level concurrency queue is saturated.
- `image_worker_start` without finish: move inward to service/Gemini logs.

### PassportService boundary (`src/passport_service.py`)

Events:
- `service_start` / `service_finish` / `service_exception`
- `base64_decode_start` / `base64_decode_finish`: decoded byte size
- `analyzer_start` / `analyzer_finish`
- `parse_start` / `parse_finish`: parsed key count
- `empty_field_retry_start` / `empty_field_retry_finish`: empty_field_count, retry_count
- `retry_attempt_start` / `retry_attempt_finish` / `retry_attempt_failure`: field, attempt, elapsed_sec

### Vision/Gemini / executor boundary (`src/vision_analyzer.py` or related)

Events:
- `executor_submit`: field, phase/executor, max_workers
- `gemini_call_start`
- `gemini_call_finish`: elapsed_sec
- `gemini_call_failure`: elapsed_sec, exception_type
- `executor_result`
- `executor_exception`

Interpretation:
- Many `executor_submit` but few/no `gemini_call_start`: ThreadPoolExecutor queue or worker exhaustion.
- `gemini_call_start` without finish/failure: Gemini/network/client blocking call or retry stall.
- Gemini finishes but no `executor_result`: result handoff back to event loop may be stuck.

## Delegating this work to Codex

When using Codex CLI for this class of instrumentation, give it explicit guardrails:
- Read `app.py`, `src/passport_service.py`, `src/vision_analyzer.py` first.
- Only add INFO diagnostics; no logic/config/API changes.
- Preserve existing dirty diff; do not touch unrelated files such as `cloudbuild.yaml`.
- Do not commit or push.
- Final answer must explain modified files, event names, why each boundary and value was chosen, and what checks were run.

After Codex exits, verify yourself:

```bash
git status --short --branch
git diff --stat
git diff --check
python3 -m compileall app.py src
```

If full tests are absent, run Flask smoke tests with a mocked passport service for `/health`, `/`, single recognize, and batch recognize to confirm trace propagation and log emission without calling Gemini.
