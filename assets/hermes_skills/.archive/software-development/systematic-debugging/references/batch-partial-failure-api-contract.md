# Batch partial-failure API contract pattern

Use this when a batch endpoint should process all valid items even if one image/document/record fails.

## Target behavior

- HTTP 200 means the batch request was accepted and all items were attempted; it does not mean every item succeeded.
- Each item result carries its own `success` flag.
- Failed items include a stable machine-readable `error_code` and a human-readable `error`.
- A single item exception must not propagate out of the per-item worker and abort the whole `asyncio.gather()` call.

## Regression test shape

Write the test first with a fake service that returns success for some inputs and raises for others:

```python
class FakeService:
    async def process(self, payload):
        if payload == "bad-input":
            raise ValueError("invalid payload")
        if payload == "service-down":
            raise RuntimeError("upstream failed")
        if payload == "unexpected":
            raise Exception("boom")
        return {"value": payload}


def test_batch_returns_per_item_failures_without_aborting(monkeypatch):
    monkeypatch.setattr(app_module, "service", FakeService())

    results = asyncio.run(app_module._process_batch([
        {"id": "OK001", "payload": "ok"},
        {"id": "BAD001", "payload": "bad-input"},
        {"id": "OK002", "payload": "ok2"},
    ]))

    assert results == [
        {"id": "OK001", "success": True, "data": {"value": "ok"}},
        {
            "id": "BAD001",
            "success": False,
            "error_code": "ITEM_INVALID",
            "error": "invalid payload",
        },
        {"id": "OK002", "success": True, "data": {"value": "ok2"}},
    ]
```

Also test unexpected exceptions if the current implementation can otherwise leak them through `asyncio.gather()`.

## Implementation checklist

- Define constants for per-item error codes near the endpoint/helper.
- Catch expected validation/input errors and map to an invalid-item code.
- Catch expected service/upstream errors and map to a retryable/service code.
- Catch `Exception` at the per-item boundary, log with item id, and return an unexpected-item code.
- Do not move this catch only to the outer endpoint handler; that still causes the entire batch to fail.
- Keep ordering stable unless the API already documents unordered results.
- Count `successful` and `failed` from the returned item list.

## Documentation checklist

Add/update docs with:

- A note that HTTP-level success and per-item success are different.
- A mixed success/failure response example.
- An error-code table with meaning and downstream recommended action.
- A note that downstream callers must inspect `results[].success`.

Example error-code categories:

| Category | Meaning | Downstream action |
| --- | --- | --- |
| `*_INVALID` | Input/content for one item is invalid. | Ask for re-upload or manual review. |
| `*_FAILED` | Upstream recognition/service failed for one item. | Retry the specific item; escalate if repeated. |
| `*_UNEXPECTED_ERROR` | Unhandled item-level error was contained. | Preserve item id and report to API owner for logs. |
