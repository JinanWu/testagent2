# Passport-recog-data Cloud Run triage notes (prod-cola-rd)

Session date: 2026-06-02
Service: `passport-recog-data`
Project: `prod-cola-rd`
Region: `asia-east1`
Revision observed: `passport-recog-data-00015-4c7`

## Symptom summary
- User reported a burst of failures on 6/1.
- Cloud Run request logs showed no HTTP errors; all observed requests returned `200`.
- The issue presented as long-running batch recognition calls, partial batch success, and poor progress visibility in logs.
- Desired observability phrases like `正在辨識` and `完成張數` were not present in the code or logs.

## What the logs showed
- Total request log sample in the window: 385 requests.
- Status distribution: `200` only.
- Latency profile:
  - median around 29s
  - many requests in the 90-125s range
  - 56 requests at or above 120s
  - max observed latency about 125.01s
- Batch log lines were present:
  - `開始批次辨識: 共 N 張圖片, IMAGE_CONCURRENCY=5`
  - `批次辨識完成: 總耗時=..., 成功=x/y, 平均每張=...`
  - `圖片 X 辨識逾時: timeout=45s`
- Partial batch successes were observed, e.g. `12/13`, `3/4`, `2/3`, `40/41`.
- Only four timeout warnings were observed in the sampled window, but the request latencies were still extreme.

## Helpful log queries
Use these exact queries when re-checking the service:

```bash
gcloud run services describe passport-recog-data \
  --project prod-cola-rd --region asia-east1

# Request latency sample
 gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND logName="projects/prod-cola-rd/logs/run.googleapis.com%2Frequests" AND timestamp>="2026-05-31T16:00:00Z" AND timestamp<"2026-06-01T16:00:00Z"' \
  --project prod-cola-rd --limit 5000 --format=json

# Text logs around batch runs
 gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND timestamp>="2026-05-31T16:00:00Z" AND timestamp<"2026-06-01T16:00:00Z"' \
  --project prod-cola-rd --limit 5000 --format=json

# Trace-level drill-down by trace ID
 gcloud logging read 'trace="projects/prod-cola-rd/traces/<TRACE_ID>"' \
  --project prod-cola-rd --limit 50 --format=json
```

## Code observations
- `src/passport_service.py` logs batch start/completion, but does not log a per-image in-progress marker.
- `src/vision_analyzer.py` uses a main Gemini thread pool and a separate retry pool.
- Relevant env values observed in Cloud Run:
  - `GEMINI_MAX_WORKERS=40`
  - `GEMINI_RETRY_WORKERS=8`
  - `IMAGE_CONCURRENCY=5`
  - `GEMINI_HTTP_TIMEOUT_MS=60000`
- Retry timeout guard exists in code; empty-field retries are capped by a total retry timeout.

## 2026-06-02 reproduction follow-up

### User correction: calls/min vs batch size
- Interpret “1 分鐘 50 筆” / “N calls per minute” as N separate API calls per minute, not one batch containing N images.
- For this passport batch API, reproduce with two independent variables:
  - calls per minute (HTTP request rate)
  - images per call (batch payload size, e.g. random 2–4 images)
- Do not jump from “50 calls/min” to a single `batch_size=50` unless the user explicitly says batch size.

### Dev reproduction evidence from 2026-06-02
- Large single-batch dev test against `dev-passport-recog.colatour.org/api/passport/recognize/batch`:
  - 50 images/request: HTTP 504 at ~107.90s
  - 100 images/request: HTTP 504 at ~108.15s
  - 200 images/request: HTTP 504 at ~110.80s
  - Shape: gateway/request timeout; not identical to prod 6/1 because prod request logs were HTTP 200.
- Rate-call dev test, each call random 2–4 images, 60-second dispatch window, client timeout 120s:
  - 10 calls/min: 10/10 calls HTTP 200, 32/32 images succeeded, avg latency ~79.23s, P95 ~109.41s.
  - 20 calls/min: 6/20 calls succeeded, 14/20 client read timeouts at ~120s, 17/59 images succeeded, 42 no-response images.
  - 30 calls/min: 0/30 calls succeeded, 30/30 client read timeouts at ~120s, 92 no-response images.
- Interpretation: dev reproduced the core queueing/120s plateau symptom, but with a harsher failure shape (client timeout or 504) than prod’s mostly HTTP 200 slow responses.
- Caveat: when scenarios run back-to-back, timed-out requests may continue executing server-side and contaminate the next scenario; add a 5–10 minute cool-down between load levels when finding a precise capacity threshold.
- Suggested next reproduction sweep: 12/15/18 calls/min, each 2–4 images, with cool-down and simultaneous Cloud Run request/app log collection.

### Prod 6/1 15:00–21:00 Cloud Run log evidence
- Window: `2026-06-01T07:00:00Z` to `2026-06-01T13:00:00Z` (Taipei 15:00–21:00).
- Request logs observed: 156, all endpoint `/api/passport/recognize/batch`.
- HTTP status: 156× HTTP 200, 0× 4xx/5xx.

### Prod 6/1 exact user-requested count, 15:01–21:33 Taipei
- Window: `2026-06-01T07:01:00Z` to `2026-06-01T13:33:00Z` (Taipei 2026/6/1 15:01–21:33).
- Cloud Run request logs observed: 157, all endpoint `/api/passport/recognize/batch`.
- HTTP status: 157× HTTP 200, 0× 4xx/5xx.
- Hourly distribution by Taipei hour:
  - 15:00: 29
  - 16:00: 22
  - 17:00: 53
  - 18:00: 19
  - 19:00: 15
  - 20:00: 18
  - 21:00–21:33: 1
- Latency profile: median ~89.96s, average ~90.38s, max ~125.01s; 91 requests >=60s, 76 >=90s, 56 >=120s.
- Report this as Cloud Run API request count only. Gemini calls/images are likely higher because a batch may contain multiple images and retry calls.

### Prod 6/1 15:00–21:00 latency summary
- Latency profile:
  - min ~29.92s
  - median ~89.97s
  - average ~90.60s
  - p95/p99/max ~125.01s
  - 91 requests >=60s, 76 >=90s, 56 >=120s.
- Hourly pattern:
  - 15:00: 29 req, median ~59.97s, 10 >=120s
  - 16:00: 22 req, median ~119.89s, 8 >=120s
  - 17:00: 53 req, median ~89.96s, 15 >=120s
  - 18:00: 19 req, median ~59.98s, 5 >=120s
  - 19:00: 15 req, median ~125.00s, 10 >=120s
  - 20:00: 18 req, median ~104.96s, 8 >=120s
- Response size for slow requests was consistently tiny (~449 bytes), consistent with partial/error-like application responses despite HTTP 200.
- App logs in that window were sparse (only ~10 non-request entries around 15:00), so request logs are the main evidence for “service stuck”; missing per-image progress logs are an observability gap.

## Likely interpretation
- The service is not failing at the HTTP layer in prod 6/1; it is suffering from slow upstream Gemini calls, queueing, and partial per-image timeouts.
- Lack of per-image progress logging makes the batch look opaque even when it is still running.
- Treat a reproduction as only “partial” when it matches latency/queueing but differs in HTTP shape (prod HTTP 200 vs dev 504/client timeout).
- For future triage, separate these layers explicitly:
  1. Cloud Run request latency,
  2. batch progress logs,
  3. per-image / retry timeout logs,
  4. HTTP status/failure shape under load.

## 2026-06-02 follow-up: diagnosing the 6/1 15:00–21:30 trigger

When asked whether a specific “restart” caused the 6/1 afternoon incident, use system/request/app logs together before assigning causality.

Observed evidence:
- No Cloud Run service update/deploy/replace audit event was found for `passport-recog-data` on 6/1; the serving revision remained `passport-recog-data-00015-4c7`.
- The 21:32 Taipei event was a normal scale-from-zero cold start, not a crash or manual restart:
  - system log: `Starting new instance. Reason: AUTOSCALING ... or no existing capacity for current traffic`
  - request log at the same second: one POST to `/api/passport/recognize/batch`
  - app startup logs followed, then `開始批次辨識: 共 13 張圖片`, one image timeout, and HTTP 200.
- In the 15:01–21:33 window, Cloud Run request logs showed 157 requests, all HTTP 200. The 15:01–20:52 requests were almost all on the same instance, with response size consistently ~449 bytes; only the later 21:32 cold-start request had a larger response (~4811 bytes). Treat repeated tiny successful responses as suspicious application-level timeout/error payloads even when HTTP status is 200.
- Around 14:58:59, just before the 15:00 plateau, app logs showed a larger 18-image batch plus many empty-field retry logs (`空欄位過多`, `將重新辨識一次`, retry timeout). This is a likely load amplifier: actual Gemini calls can greatly exceed image count when field-level retries fan out.
- The Cloud Run configuration observed during triage was a risky shape for this workload:
  - `autoscaling.knative.dev/maxScale: 1`
  - `containerConcurrency: 80`
  - `cpu: 1`, `memory: 1Gi`
  - `IMAGE_CONCURRENCY=5`, `BATCH_SIZE=100`, `GEMINI_MAX_WORKERS=40`, `GEMINI_RETRY_WORKERS=8`
  This lets many HTTP requests enter one instance while each request can fan out to multiple image recognitions and retries.

Interpretation to use:
- The most likely trigger for the 6/1 15:00–21:30 issue was not a restart; it was cumulative single-instance queueing from batch traffic plus Gemini fan-out/retry pressure.
- Phrase causality as: “batch request volume and retry fan-out exceeded the practical capacity of one Cloud Run instance; because maxScale=1 and there was no global admission control, requests accumulated into 60/90/125s latency plateaus and application-level timeout-like responses.”
- Do not describe a later cold start after idle time as the cause of an earlier sustained incident.
