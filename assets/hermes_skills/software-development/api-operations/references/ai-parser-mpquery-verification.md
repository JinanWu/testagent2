# AIParserMpQuery verification notes

Session-derived notes for verifying `POST https://fpmcmp.colatour.org/AirTicket/ExtVendor/AIParserMpQuery`.

## Request shape
- Method: POST
- Headers:
  - `Accept: application/json`
  - `Content-Type: application/json`
  - `Device_Name: Browser`
- Body shape:
  ```json
  {
    "Request_Details": {
      "Departure_Date": "YYYY-MM-DD",
      "Return_Date": "YYYY-MM-DD",
      "Departure_Airport": "HND|NRT|TPE",
      "Arrival_Airport": "HND|NRT|TPE",
      "Cabin_Class": "經濟艙|豪華經濟艙|商務艙|頭等艙"
    },
    "KeyValue": "SHA512(yyyyMMdd+ColaMPToAIParser) uppercase hex"
  }
  ```

## Observed behavior
- GET returns: `{"Message":"The requested resource does not support http method 'GET'."}`
- Missing/incorrect auth shape returns: `{"AlertMsg":"ClientId Disallowed"}`
- Correct request returns HTTP 200 with a JSON string body; decode twice:
  1. outer JSON value is a string
  2. inner string is the actual JSON object

## Verification pattern
- Use the smallest useful filter set.
- Repeat the exact same request 2–3 times to check stability of:
  - HTTP status
  - row count
  - latency
  - key fields (`kp`, `kp_model`, `activity_fee_adjustment`)
- Sample a representative row where `kp_model == "2"` if the question is about field coverage.

## Session observations
- With `Departure_Date=2026-08-06`, `Return_Date=2026-08-13`, and `KeyValue = SHA512(20260609ColaMPToAIParser)`:
  - `TPE -> NRT` returned 342 rows consistently on repeated requests.
  - `TPE -> HND` returned ~318–336 rows on repeated requests.
  - Observed latency was roughly 14–22s per request.
  - `kp_model` values included `1` and `2`.
  - `activity_fee_adjustment` values observed: `10.0`, `200.0`, `299.0`.
