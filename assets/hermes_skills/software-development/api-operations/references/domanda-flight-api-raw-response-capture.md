# Domanda flight API raw response capture

Use this when investigating Domanda flight-fare display issues where the user asks for the API response that Domanda received, especially airline/route/date/time mismatches.

## Key distinction

Do not substitute downstream ETL, BigQuery, or Cloud SQL validation for this task. The requested artifact is the raw API response received during the Domanda search flow. ETL tables can be useful later, but they do not prove what the website/API returned at query time.

## Workflow

1. Identify the live Domanda search path.
   - Find the frontend/backend repo or use the browser Network panel on the Domanda site.
   - Capture the real endpoint, method, headers, query params/body, auth/session requirements, and environment.

2. Reproduce the exact query conditions.
   - Preserve route/airport, date range, airline, cabin/passenger params, and any displayed time filter.
   - If a time such as `09:55` appears in Domanda, determine whether it is part of the API request or only a frontend filter/display value.

3. Save auditable artifacts.
   - Raw request metadata.
   - Raw response body as returned by the API.
   - A small decoded/extracted sample for the relevant airline/segment/time.
   - File paths and timestamps for replay.

4. Compare raw response against UI display.
   - Check outbound vs inbound leg/segment index.
   - Check departure vs arrival time fields.
   - Check timezone/local-time conversion, UTC offsets, cross-day arrival/departure, and date-boundary handling.
   - Check whether the UI transformed or filtered a different raw value into the displayed time.

5. Report compactly.
   - Query conditions and environment.
   - Endpoint and request shape, redacting secrets.
   - HTTP status and response artifact path.
   - 2-3 relevant response rows/segments only.
   - Conclusion: raw API already has the displayed value, frontend/backend transform changed it, or the target flight was not found.

## Example condition shape

- Destination/airport: AMS
- Date range: 2026-08-05 to 2026-08-10
- Airline: CX
- Target displayed return time: 09:55

The specific values above are a pattern example; always use the user's current query values.
