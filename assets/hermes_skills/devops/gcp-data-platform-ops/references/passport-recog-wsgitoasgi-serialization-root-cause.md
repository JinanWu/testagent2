# Passport-recog-data WsgiToAsgi serialization root-cause pattern

Use this reference when Cloud Run request logs show many long-latency HTTP 200 requests for `passport-recog-data`, while clients/Cloudflare see timeouts or the service appears stuck.

## Symptom shape

- Cloud Run request logs can remain all HTTP 200 even while clients time out.
- Latency plateaus around client/proxy boundaries, commonly 90-125s; in origin tests it may extend to ~180s.
- `/health` may also time out or show high latency after a burst, proving the issue is not limited to the batch-recognition endpoint.
- The failure can be reproduced with many small batch calls, not only one huge batch:
  - 10 calls/min with 2-4 images may still succeed but p95 can exceed 100s.
  - 20 calls/min can produce many client timeouts.
  - 30 calls/min can produce all client timeouts.

## Most important diagnostic signal

Instrument and compare these boundaries:

1. ASGI outer middleware before `WsgiToAsgi` enters the WSGI app:
   - `ASGI_ACCEPT path=... trace_id=... thread_name=MainThread task=...`
2. Flask `before_request` / handler entry:
   - `REQ_START ... inflight=... thread_name=ThreadPoolExecutor-0_0`
   - `BATCH_HANDLER_START ...`
3. Handler and batch milestones:
   - `BATCH_PROCESS_START`, `IMG_RECOG_START`, `IMG_TIMEOUT`, `BATCH_PROCESS_END`, `REQ_END`

If many traces have `ASGI_ACCEPT` but no `REQ_START`, or if `ASGI_ACCEPT -> REQ_START` delay grows from seconds to minutes, the request is queued before the Flask handler. This points to WsgiToAsgi / WSGI dispatch serialization rather than Gemini processing.

Concrete observed pattern:

- 219 traces with `ASGI_ACCEPT`.
- Only 35 reached `REQ_START` / `BATCH_HANDLER_START`.
- 184 had `ASGI_ACCEPT` only.
- `ASGI_ACCEPT -> REQ_START` delay:
  - median ~80s
  - p95 ~299s
  - max ~318s
- `REQ_START` logs all showed `thread_name=ThreadPoolExecutor-0_0` and `inflight=1`, indicating effective single-threaded WSGI handler execution despite Cloud Run concurrency being much higher.

## Distinguish from Gemini/executor saturation

Check executor snapshots around image/retry timeouts:

```text
EXECUTOR_SNAPSHOT label=IMG_TIMEOUT main_queue=0 main_workers=40 retry_queue=0 retry_workers=8
EXECUTOR_SNAPSHOT label=RETRY_TIMEOUT main_queue=0 main_workers=40 retry_queue=0 retry_workers=8
```

If executor queues stay at 0 while request latency explodes and traces are stuck before `REQ_START`, Gemini thread-pool saturation is not the primary root cause. Individual Gemini calls may still be slow, but they are secondary.

## Why app-level batch timeout may not help

A timeout such as:

```python
await asyncio.wait_for(_process_batch_recognition(images), timeout=105)
```

starts only after the handler begins. If requests spend 120-300s between `ASGI_ACCEPT` and `REQ_START`, the app-level timeout has not started yet. Client/Cloudflare timeouts can therefore happen before the batch timeout is even active.

## Interpretation

For Flask WSGI wrapped with `asgiref.wsgi.WsgiToAsgi`, Cloud Run may accept many concurrent requests, but the WSGI app can be dispatched through an effectively serialized executor. With Cloud Run `concurrency=80` and `max-instances=1`, this lets many requests enter the instance and queue internally while only one Flask handler progresses. The queue blocks `/health` too, so the instance looks stuck.

## Fix direction

Root-cause fixes:

1. Replace Flask WSGI + `WsgiToAsgi` with a real ASGI app such as FastAPI, Starlette, or Quart; keep request/response schema stable where possible.
2. Or revert to a coherent synchronous WSGI model with explicit worker/thread capacity and Cloud Run concurrency aligned to that capacity.

Required safeguards even after an ASGI migration:

- Global admission control: return 429/503 quickly when active batch/request capacity is exhausted.
- Keep image-level semaphore but also limit concurrent batch requests.
- Set Cloud Run concurrency to match real app capacity; do not leave it at 80 if the app can process only a few long-running calls.
- Increase max instances only with explicit cost caps.
- Prefer async job API for multi-image Gemini work where user-visible HTTP deadlines are short.

## Reporting checklist

When reporting this pattern, include:

- Request-log status distribution and latency p50/p95/p99/max.
- Counts of request logs by path, especially `/health` vs `/api/passport/recognize/batch`.
- Counts of `ASGI_ACCEPT`, `REQ_START`, `REQ_END`, `BATCH_HANDLER_START`, `BATCH_PROCESS_START`, `IMG_TIMEOUT`, and `EXECUTOR_SNAPSHOT`.
- `ASGI_ACCEPT -> REQ_START` delay stats and examples with trace IDs.
- Whether `REQ_START` shows a single thread such as `ThreadPoolExecutor-0_0` and `inflight=1`.
- Executor queue sizes to rule in/out Gemini executor saturation.
- Whether a later `STARTUP_CONFIG` indicates instance replacement/recovery.
