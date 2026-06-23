# Prod Vertex AI cost estimation from Cloud Run logs

Session note: for Cloud Run services that batch-process images and call Gemini/Vertex AI, cost can be estimated from request logs plus app logs even when billing export is unavailable.

## Estimation recipe
1. Count Cloud Run requests in the target window.
2. Prefer batch-log image counts over request count when each request fans out to multiple images.
3. Multiply image count by the number of model calls per image.
   - In passport-recog-data, each image triggers 5 Gemini calls for 5 fields.
4. Apply the model price to approximate input/output tokens.
5. Add a retry premium when logs show `429 RESOURCE_EXHAUSTED`, empty-field rechecks, or repeated retries.

## Observed heuristics from the prod passport-recog-data session
- Request count alone overstates uncertainty and can hide batch-size variation.
- Image count from batch logs is the better base metric.
- Retry / quota pressure can meaningfully increase actual spend and latency.
- A service can return 200 while still being expensive and slow because the upstream GenAI work is the bottleneck.

## Useful log signals
- `RESOURCE_EXHAUSTED`
- `429`
- empty-field recheck warnings
- long latency with successful request status

## When this is enough
Use this method when you need a fast cost range and you do not yet have Cloud Billing Export / BigQuery cost tables. Replace the estimate with billing export as soon as it is available.
