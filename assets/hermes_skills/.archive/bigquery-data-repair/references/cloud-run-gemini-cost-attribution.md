# Cloud Run + Gemini cost attribution from request logs

Session pattern for prod cost questions when direct BigQuery billing export access is limited.

## Situation
- The service was a Cloud Run app doing Gemini/Vertex AI inference.
- Billing CSV/export data was available as a monthly report, but not always as a queryable raw export from the active account/project.
- Cloud Run request logs were accessible and provided request counts, status codes, timestamps, request sizes, and URLs.

## Reusable approach
1. Confirm the exact Cloud Run service name, region, and latest revision.
2. Read Cloud Run request logs for the target month window.
3. Count requests by day and status code to establish whether the service is actually active and whether errors are isolated or systemic.
4. Inspect `requestUrl` to separate business traffic from probes/scanners.
5. Use the monthly billing CSV/report to identify Gemini/Vertex AI SKU families.
6. Align the log-derived request totals with daily billing rows to compute:
   - total month cost
   - cost per request
   - thinking cost per request
   - before/after deltas around a change date
7. If the code path performs multiple Gemini calls per business request, do not assume Cloud Run request count equals model-call count.

## Important findings from this session
- A batch endpoint can hide many model calls: in this service, one uploaded passport image triggered 8 Gemini calls, one per field.
- Larger batch payloads correlated with 5xx responses and long latency.
- Cost reduction can be real even when request volume rises, as long as unit cost drops.
- The most useful comparison window was split around the change date and excluded the transition day noise.

## Pitfalls
- Do not equate batch HTTP requests with model calls.
- Do not interpret a single monthly CSV total as day-granular evidence.
- Do not assume lack of direct billing export access means the analysis cannot proceed; request logs plus a billing CSV can still support a useful attribution.
- Large payloads and partial failures can distort cost-per-request if you ignore error rates.

## Verification checklist
- Service status and revision are confirmed.
- Request count, status split, and URL distribution are known.
- Billing SKUs are grouped into meaningful families (e.g. thinking vs non-thinking).
- Before/after windows are compared on a per-request basis, not only in absolute totals.
