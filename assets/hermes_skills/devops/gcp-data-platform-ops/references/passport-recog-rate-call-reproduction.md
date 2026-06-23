# Passport-recog-data rate-call reproduction notes

Session date: 2026-06-02
Service class: Cloud Run + Gemini batch inference (`passport-recog-data`)
Projects observed: `prod-cola-rd`, `dev-cola-rd`
Region: `asia-east1`

## Why this matters

For this service, "50 requests per minute" should be modeled as many API calls per minute, not one large batch with 50 images. The useful reproduction shape is:

- N API calls per minute
- each call carries a small random batch of 2-4 images
- client timeout set to 120s
- compare local client outcomes with Cloud Run/app logs

This revealed the core failure mode: request backlog / slow upstream Gemini calls cause clients to timeout first, while the server may continue processing queued requests and emit app logs later.

## Reproduction pattern

Use a rate-call harness that schedules requests over 60 seconds:

- 10 calls/min: dispatch every ~6s
- 20 calls/min: dispatch every ~3s
- 30 calls/min: dispatch every ~2s
- payload: `POST /api/passport/recognize/batch` with 2-4 random images
- timeout: 120s on the client
- record raw responses, image manifest, per-call latency, status, timeout flag

Important pitfall: do not interpret "50/min" as a single API call with 50 images unless the user explicitly asks for a large-batch test. For operational load, use multiple calls with small random batches.

## Local reproduction setup used

Run the repo locally with Hypercorn and Cloud Run-like environment values:

```bash
cd /path/to/passport-recog-data
PROJECT_ID=dev-cola-rd \
GEMINI_MAX_WORKERS=40 \
GEMINI_RETRY_WORKERS=8 \
IMAGE_CONCURRENCY=5 \
BATCH_SIZE=100 \
BATCH_TIMEOUT_SECONDS=105 \
IMAGE_TIMEOUT_SECONDS=45 \
hypercorn app:asgi_app --bind 127.0.0.1:8080
```

Then target:

```text
http://127.0.0.1:8080/api/passport/recognize/batch
```

Verify health first:

```text
GET http://127.0.0.1:8080/health -> 200 {"status":"healthy"}
```

## Observed local reproduction result

Local Hypercorn run with 2-4 images per call and 120s client timeout:

| calls/min | total calls | total images | success calls | success images | client timeout calls | client timeout images | avg latency | p95 latency |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 32 | 7 | 23 | 3 | 9 | 75.34s | 120.03s |
| 20 | 20 | 59 | 5 | 12 | 15 | 46 | 111.35s | 120.03s |
| 30 | 30 | 92 | 0 | 0 | 30 | 92 | 120.03s | 120.03s |

This is enough to say the issue can be reproduced locally: the synchronous batch API queues/blocks until the client timeout boundary.

## Dev Cloud Run comparison

Dev Cloud Run rate-call run with the same shape:

| calls/min | calls | images | success calls | client timeout calls | avg latency | p95 |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 10 | 32 | 10 | 0 | 79.23s | 109.41s |
| 20 | 20 | 59 | 6 | 14 | 101.31s | 120.99s |
| 30 | 30 | 92 | 0 | 30 | 120.22s | 120.37s |

Dev Cloud Run request logs may still show HTTP 200 at ~120s even when the local client reports read timeout. Always compare client raw result with Cloud Run request logs.

## Prod 6/1 symptom comparison

For prod 2026-06-01 15:00-21:00 Taipei:

- request logs: 156 requests, all HTTP 200
- median latency ~89.97s
- p95/p99/max ~125.01s
- 56/156 requests >=120s
- app logs were sparse, so request latency was the primary evidence

Interpretation: local/dev tests reproduced the key queueing/timeout plateau, but HTTP surface can differ:

- prod: mostly/entirely HTTP 200 with severe latency plateau
- dev/local client: read timeout at 120s, sometimes Cloud Run still logs 200
- large-batch dev test: HTTP 504 around 108-111s

Do not require exact HTTP status equality to call the failure mode reproduced; compare latency plateau, timeout boundary, and server-side work continuing after client timeout.

## Critical timing lesson

When client raw response says `The read operation timed out`, the server may continue processing. In observed logs:

1. client dispatched request
2. client timed out after ~120s
3. app later logged `開始批次辨識`
4. app then logged image-level timeout or batch completion

So phrase causality carefully:

- Correct: backend queueing/slow processing made the client give up first; backend continued and logged later.
- Incorrect: client timeout caused the backend to enter image timeout.

## What to inspect in logs

For Cloud Run:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND logName="projects/<PROJECT>/logs/run.googleapis.com%2Frequests" AND timestamp>="<START_Z>" AND timestamp<"<END_Z>"' \
  --project <PROJECT> --limit 10000 --format=json

gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="passport-recog-data" AND timestamp>="<START_Z>" AND timestamp<"<END_Z>"' \
  --project <PROJECT> --limit 10000 --format=json
```

Summarize:

- status distribution
- latency min/median/avg/p95/p99/max
- counts >=60s, >=90s, >=120s
- hourly distribution
- slowest traces/request sizes
- app log counts for `開始批次辨識`, `批次辨識完成`, `辨識逾時`, `重試`, `Gemini`, `ERROR`

## Mitigation hypotheses to validate locally first

1. App-level active request or active image limit with fast 429/503 when busy.
2. Request deadline below client timeout (e.g. 90-100s) returning partial results instead of letting clients read-timeout.
3. Lower server/container concurrency so requests do not all enter app internals and accumulate stale work.
4. Add per-request/per-image progress logs: queued, started, image start, Gemini call start/end, retry start/end, completed, timeout.
5. Consider async job API for long-running batch recognition; synchronous HTTP is fragile for multi-image Gemini work.

## Reporting preference

When reporting these tests, include:

- calls/min, total calls, total images
- success calls and success images
- client timeout calls and affected images
- avg and p95 latency
- whether HTTP status differs between client and Cloud Run logs
- whether app logs show server work after client timeout
