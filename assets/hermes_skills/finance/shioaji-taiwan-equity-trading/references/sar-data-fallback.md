# SAR data-source fallback notes

Use this when calculating daily SAR for Taiwan stocks and the TWSE monthly `STOCK_DAY` endpoint is missing coverage or is awkward for the requested code/date span.

## Preferred order
1. TWSE monthly `STOCK_DAY` endpoint for TWSE-listed stocks.
2. Yahoo Finance chart endpoint as a practical fallback for daily OHLC bars.

## Yahoo Finance fallback pattern
- Symbol format: `{code}.TW`
- Endpoint shape: `https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=6mo&interval=1d&includePrePost=false&events=div%2Csplits`
- Filter returned bars by the requested `start_date` / `end_date`.
- Ignore rows whose `close` is `null` (Yahoo can return a trailing incomplete candle).

## SAR computation reminder
For long-side SAR, keep the existing rule in the main skill:
- check `low <= current_sar` before updating EP/AF for that bar
- if not hit, update EP on new highs
- next SAR is `sar + af * (ep - sar)`
- constrain the next SAR with the current and previous lows

## Verified use case
This fallback produced usable daily bars for:
- `3711.TW` (日月光投控)
- `6239.TW` (力成)

That allowed a full 2026-05-04 → 2026-06-22 SAR run when the direct TWSE path was not convenient.
