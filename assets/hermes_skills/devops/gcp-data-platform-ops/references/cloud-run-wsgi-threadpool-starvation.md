# Cloud Run WSGI/ASGI threadpool starvation pattern

Use this reference when a Cloud Run GenAI/image-recognition service shows request latency and received/request bytes in logs or Monitoring, but application logs do not show the expected route/batch-start marker.

## Symptom shape

- Cloud Run request logs show POST requests, request bytes, and long latency plateaus, often around client/proxy timeout boundaries.
- Application logs are sparse or missing the expected first business marker, e.g. `開始批次辨識`.
- HTTP status may still be 200 if the application or upstream wrapper returns a fixed failure envelope.
- Response sizes may collapse to a small repeated value, suggesting a fixed timeout/error response rather than normal payloads.

## Investigation checklist

1. Confirm whether the request reached the application route.
   - Add/log a marker at route entry before JSON parsing.
   - Add/log markers after `request.get_json()`, after validation, before batch processing, and at batch start.
   - If Cloud Run has latency but no route-entry log, investigate server/adapter ingress before business logic.

2. Inspect the serving stack.
   - Check Docker/entrypoint for `hypercorn`, `uvicorn`, `gunicorn`, worker count, and whether the app is native ASGI or WSGI wrapped as ASGI.
   - For Flask wrapped with `asgiref.wsgi.WsgiToAsgi`, remember `sync_to_async` defaults to `thread_sensitive=True`, which can serialize WSGI execution through a single-thread executor unless configured otherwise. This can make requests wait before the route marker is emitted.
   - Do not assume `hypercorn app:asgi_app` means the application is truly concurrent if the underlying app is WSGI.

3. Inspect blocking work inside the route.
   - Look for `loop.run_in_executor(...)` around synchronous SDK calls.
   - `asyncio.wait_for()` cancels the coroutine/future wait, but it does not forcibly stop a Python thread already running a blocking synchronous SDK call.
   - A bounded `ThreadPoolExecutor` can suffer worker starvation without leaking unbounded threads: workers remain occupied until the blocking call returns, while new work queues behind them.

4. Quantify fan-out.
   - Per request: images per batch × fields/calls per image × retry calls.
   - Compare that with `GEMINI_MAX_WORKERS`, retry executor workers, Cloud Run `containerConcurrency`, worker count, CPU, and `maxScale`.
   - If `containerConcurrency` is much higher than safe app-level concurrency, Cloud Run can admit more requests than the single instance can process.

5. Add temporary executor diagnostics.
   - Log executor `_max_workers`, `len(_threads)`, and `_work_queue.qsize()` at route entry, batch start, image start, image timeout, and Gemini call start/end.
   - Log thread name and field/image id for each blocking model call.
   - Log `asyncio.CancelledError` boundaries so coroutine cancellation can be distinguished from blocking thread completion.

## Interpretation

This is usually not a traditional thread leak. It is more often bounded threadpool starvation plus ingress/adapter queueing:

- Cloud Run or the ASGI server accepts the request/body.
- The WSGI adapter or route worker is busy, so business logs have not started yet.
- Already-started synchronous model calls keep occupying threadpool workers even after higher-level async timeouts.
- Later requests accumulate latency without corresponding business-start logs.

## Mitigation options

- Lower Cloud Run `containerConcurrency` to the app's proven safe concurrency.
- Increase `maxScale` cautiously and verify upstream quota/cost caps.
- Add application-level admission control: return `429` plus `retry_after_seconds` when active/queued work exceeds thresholds.
- Prefer native ASGI for long-running async services, or move recognition to Cloud Tasks / job queue with job-id polling.
- Prefer truly async model clients where available, or enforce SDK/client timeouts shorter than image/request timeout; do not rely on `asyncio.wait_for()` to kill blocking threads.
