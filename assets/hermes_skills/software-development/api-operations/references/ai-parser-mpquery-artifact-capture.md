# AIParserMpQuery artifact capture notes

Use when the task is to verify the travel-ticket API result itself, not the downstream BigQuery table.

## Correct task framing

If a reminder or user request mentions `domanda`, `flypaUAT`, `AIParserMpQuery`, `kp`, `kp_model`, or `activity_fee_adjustment`, first treat it as an API-response verification task unless the user explicitly asks for BigQuery/ETL validation.

Typical endpoint:

- `POST https://fpmcmp.colatour.org/AirTicket/ExtVendor/AIParserMpQuery`

Typical reminder-derived conditions observed:

- Date range: `2026-08-06` to `2026-08-13`
- Airports: query both `TPE -> NRT` and `TPE -> HND` when the task says HND/NRT
- Cabin: `經濟艙` unless the user gives another cabin
- Airline focus: 華航 / China Airlines, usually detectable by `CI` in flight data
- Fields to verify/extract: `kp`, `kp_model`, `activity_fee_adjustment`

## Request pattern

Headers:

```json
{
  "Accept": "application/json",
  "Content-Type": "application/json",
  "Device_Name": "Browser"
}
```

Body:

```json
{
  "Request_Details": {
    "Departure_Date": "YYYY-MM-DD",
    "Return_Date": "YYYY-MM-DD",
    "Departure_Airport": "TPE",
    "Arrival_Airport": "NRT|HND",
    "Cabin_Class": "經濟艙"
  },
  "KeyValue": "[REDACTED]"
}
```

Generate `KeyValue` as uppercase SHA512 of `yyyyMMdd + ColaMPToAIParser`. Never store the real key in artifacts or final messages; store `[REDACTED]` in manifest/metadata.

## Artifact set to save

For each route/request, save all of these under a dated run directory, e.g. `~/Downloads/domanda_ai_parser_mpquery_YYYY-MM-DD/`:

- `manifest.json` — endpoint, run timestamps, safe headers, safe request payloads, file paths, status/latency summaries
- `<label>.raw_response.txt` — exact response body as returned
- `<label>.decoded_response.json` — response decoded once or twice as needed
- `<label>.metadata.json` — HTTP status, elapsed seconds, safe request payload, artifact paths
- `<label>.summary.json` — counts and field samples
- `china_airlines_rows_for_manual_check.csv` — flattened 華航 rows for manual website comparison

Recommended CSV columns:

- `query_label`
- `departure_date`
- `return_date`
- `creation_time`
- `gds_type`
- `ticket_rule_type`
- `ticket_price`
- `kp`
- `kp_model`
- `discount`
- `ticket_price_markup_percentage`
- `tax`
- `tax_markup_percentage`
- `activity_fee_adjustment`
- `final_price`
- `flight_info`

## Decode and filtering notes

- The API may return HTTP 200 with an outer JSON string containing the actual JSON object. Decode twice before deciding the response is malformed.
- Extract `Data` rows and detect 華航 with `CI`, `中華航空`, or `華航` anywhere in the row/flight segments.
- Summarize both total returned rows and 華航-like rows.
- Count `kp_model` values among 華航 rows.
- Sample `kp` and `activity_fee_adjustment`; check whether `activity_fee_adjustment` is within int16 range when that is part of the task.

## Pitfalls

- Do not substitute a BigQuery table check for this task. The user may want to compare API output against the website manually, so raw API artifacts are the deliverable.
- Do not only save an aggregate summary; save raw and decoded API responses as well.
- Do not leave the real `KeyValue` in scripts, manifests, metadata, or final answers.
- If local Python CA-chain verification fails for the endpoint, use HTTPS with an explicit unverified SSL context only as a pragmatic capture workaround, and record that in `manifest.json` so provenance is clear.
