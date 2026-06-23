# Passport-recog-data Cloud Run slow-batch gap pattern

Use this when a production Cloud Run batch request is technically successful (HTTP 200) but the user reports that a small number of images suddenly take ~60s each or the whole batch stretches into minutes.

What to check:
1. Confirm the service is still Ready and traffic is on the latest revision. If so, do not assume deployment is still in progress.
2. Split logs by time window and isolate the exact batch request(s) after the cutoff.
3. Read the app-level batch markers first:
   - `開始批次辨識: 共 N 張圖片, IMAGE_CONCURRENCY=...`
   - `批次辨識完成: 總耗時=...s, 成功=.../...`
4. Correlate those markers with inner model-call logs, especially `HTTP Request: POST ... generateContent`.
5. Look for a long silent gap between internal model calls. A dense burst of calls followed by a 1–3 minute gap usually means the slowdown is inside the upstream model call/retry path, not Cloud Run rollout.

Session example:
- A 4-image batch returned 200 but took 253.02s total (63.25s/image average).
- The same request produced 32 Gemini generateContent calls.
- The internal call stream had a 147.6s gap, then an additional 84.1s gap before completion.
- This pattern pointed to upstream call stall/retry queueing rather than deployment activity.

Rule of thumb:
- If the request completes successfully but the internal call cadence has a long gap, diagnose the upstream dependency and retry behavior before touching Cloud Run deployment settings.
