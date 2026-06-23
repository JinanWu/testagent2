# Model inference repo review checklist

This note captures a reusable review pattern for model-serving or model-adjacent ETL repos.

## What to verify

- Identify the active repo copy first when multiple migration or test mirrors exist.
- Read the true runtime entrypoint before trusting README claims.
- Compare documented label counts / output schema against live constants in code.
- Check for request guards on missing JSON, malformed payloads, and parallel-array length mismatches.
- Check for silent fallback behavior:
  - model/API failure -> all-false labels
  - empty dict / empty list -> downstream treated as valid output
  - `unknown` labels without metrics or alerts
- Confirm every external call has a clear failure path and latency risk is acceptable.
- Run a syntax/import check (`py_compile`, build, or equivalent) before deeper behavioral work.

## Failure patterns seen in practice

- README says 17 labels while code returns 25.
- `request_json['message']['data']` accessed without null guards.
- Length checks cover only some arrays, but later code indexes others.
- A prediction exception is swallowed and converted into default labels.
- Runtime text parsing assumes one response format while the model may return another.

## Suggested reviewer output

When reporting, separate:
1. what the code actually does,
2. where the docs disagree,
3. whether failures are loud or silent,
4. whether downstream ETL can distinguish a real prediction from a fallback.
