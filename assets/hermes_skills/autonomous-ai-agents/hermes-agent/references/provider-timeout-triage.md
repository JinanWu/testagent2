# Provider timeout triage

This note captures a recurring Hermes failure shape seen in the field: a request starts, then the provider emits no usable response for a long time, and Hermes eventually aborts and retries.

## Symptom pattern

Typical logs look like:
- `No response from provider for 90s (non-streaming, model: ...)`
- `Non-streaming API call stale for ...s`
- `APIConnectionError`
- `Connection error.`
- multiple retries with the same provider/base URL

## Interpretation

- If auth is valid and other requests to the same provider sometimes succeed, treat this as a stalled or flaky provider path first.
- Do not assume credential failure just because the final surfaced error is `APIConnectionError`.
- A healthy-looking provider can still hang long enough for Hermes to kill the request and retry.

## Quick triage

1. Check `hermes doctor` for auth/config status.
2. Inspect recent logs for the exact provider, model, and base URL.
3. Decide whether this is a primary-model problem or an auxiliary-model problem.
   - Auxiliary tasks include compression, session search, and summarization.
   - If auxiliary `auto` has no fallback, the failure can cascade.
4. If the same provider is the only configured path, add a fallback provider or switch the auxiliary provider off `auto`.
5. Prefer interpreting repeated long stalls as provider/network instability rather than a one-off bad prompt.

## Useful log anchors

- `run_agent: OpenAI client created ... provider=... base_url=... model=...`
- `API call failed (attempt N/3) ... error_type=APIConnectionError`
- `Auxiliary ... connection error on auto and no fallback available`

## What to record in future incidents

Capture these fields when filing a follow-up:
- provider
- model
- base URL
- whether the call was streaming or non-streaming
- whether the failure was on the main path or an auxiliary path
- whether fallback providers were configured
