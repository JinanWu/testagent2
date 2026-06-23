# Cola / Domanda AIParser raw response capture

Use this when the user asks to reproduce a Domanda/Cola flight API result and save the actual response received from `AIParserMpQuery` for comparison with Domanda UI or ETL output.

## Endpoint and auth pattern

- Endpoint: `https://fpmcmp.colatour.org/AirTicket/ExtVendor/AIParserMpQuery`
- Method: `POST`
- Headers:
  - `Accept: application/json`
  - `Device_Name: Browser`
  - `Content-Type: application/json`
- Body shape:

```json
{
  "Request_Details": {
    "Departure_Date": "YYYY-MM-DD",
    "Departure_Airport": "TPE",
    "Arrival_Airport": "AMS",
    "Return_Date": "YYYY-MM-DD",
    "Cabin_Class": "經濟艙"
  },
  "KeyValue": "<SHA512>"
}
```

- KeyValue formula observed in `cola-tour-fare-scraper-data/flight_api_client.py`:
  - `SHA512(yyyyMMdd + "ColaMPToAIParser")`, uppercase hex.

## Capture workflow

1. Work inside the provided scraper repo, not the downstream `domanda-etl-data`, when the request is to get the API response.
2. Reuse the repo’s request-building logic if available (`FlightAPIClient._build_request_payload`, `API_HEADERS`, `API_URL`) or mirror it exactly.
3. For each relevant cabin class, save all artifacts under an `artifacts/<case-name>/` directory:
   - full request JSON
   - redacted request JSON with `KeyValue` replaced by `<redacted>`
   - raw response text before parsing
   - decoded response JSON; decode twice if the HTTP JSON body is itself a JSON string
   - run summary with endpoint, conditions, HTTP status, request-id header, elapsed seconds, response byte length, API `Status_Code`, and record count
4. Flatten `flight_segments.return` into a CSV so time/airline/flight-number mismatches can be inspected without reading huge JSON.
5. When checking a UI claim like “CX return 09:55”, do not only filter rows by time. Separately count:
   - rows/segments containing target airline or flight-number prefix, e.g. `CX`
   - rows/segments containing target time, e.g. `09:55`
   - rows/segments where both match on the same return segment/itinerary
6. Write a short `REPORT.md` summarizing exact conditions, counts, matched rows, and artifact paths.

## Pitfalls

- Domanda UI time/airline anomalies may come from frontend or ETL transformation even when the raw API response is correct. Preserve raw API output first before debugging downstream layers.
- The API can return large payloads and nested return segments; use flattened CSV plus small JSON summaries instead of pasting full responses into chat.
- A target time can exist for another airline (e.g. `09:55` for `VN`) while target airline (`CX`) exists at other times. Treat these as separate facts unless the same segment has both.
- Python on macOS can hit local CA verification issues. Prefer a proper CA bundle such as `certifi.where()` in a custom SSL context before considering an insecure/no-verify workaround, and record whether SSL verification was enabled in the run summary.
