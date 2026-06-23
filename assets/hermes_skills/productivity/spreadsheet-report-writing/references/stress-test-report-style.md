# Stress-test Markdown report style notes

Use when turning spreadsheet pressure-test results into a formal Markdown report, especially for API/model stability tests.

## Scope handling

- If the spreadsheet/result file has measured rows and the plan contains additional unmeasured experiments, do not automatically include the unmeasured plan in the report.
- Include unmeasured items only when the user explicitly wants gap analysis or plan completion tracking.
- If the user says unmeasured plans are noise, remove them from scope tables, pass/fail sections, recommendations, appendices, and aggregate counts.

## Per-test analysis shape

For each measured scenario, use this pattern:

```md
### N.N Scenario name

目的：Explain what this test is intended to reveal, why the scenario matters in production, and what signals/thresholds the reader should watch. Avoid a one-sentence generic purpose.

<table or metric summary>

觀察：

Narrative paragraph(s), not bullets. Describe the progression and operational feel: stable baseline, nearing boundary, cliff drop, recovery, mismatch, or saturation. Tie the story to concrete metrics.

結論：

Paragraph conclusion tied to the numbers and operational implication.

重點：本測試測出的極限是「...」。
```

## Latency thresholds

- Treat domain-specific time limits as hard risk lines. Example: if one image has a 45s recognition limit, P95/max near or above 45s is already dangerous.
- Wording to use: `由於單張圖片的辨識時間限制就是 45 秒，P95 或最大延遲接近 / 超過 45 秒時，應視為 timeout 危險區，而不是單純偏慢。`
- If batch latency is high but contains multiple images, clarify that batch duration is not identical to per-image timeout, while still flagging gateway/client timeout and user-wait risk.

## Request success vs item success

- Always separate item/image recognition success from API/HTTP response success.
- If item success is 100% but HTTP 200 is 0 / 5xx > 0, do **not** automatically call it a data contradiction. First interpret the domain semantics: it may mean the backend completed recognition but the synchronous API timed out before returning a response.
- Wording to use when confirmed: `圖片成功率 100% 代表所有圖片都有成功被辨識出來；API / HTTP 成功率 0% 是因為處理時間過長導致 API 超時，呼叫端沒有拿到成功 response。這不是辨識失敗，而是「後端辨識完成」與「同步 API 成功回應」分離。`
- Explain why this matters operationally: downstream users still cannot rely on synchronous calls if they cannot receive the result, even when backend recognition eventually completes. Recommend async jobs, job IDs, progress polling, result lookup, or timeout changes for these scenarios.

## Aggregate recalculation and replacement datasets

When removing a measured scenario from the report scope, recompute all dependent totals: experiment count, request count, item/image count, success count, HTTP 200 count, success rates, and token totals.

When the user supplies a retest/replacement spreadsheet because an earlier result was wrong, treat the replacement as authoritative for that scenario. Update the scenario rows everywhere: executive summary, usage-limit table, main result table, per-test analysis, error/latency analysis, pass/fail classification, appendix, and generated `.docx` / `.html` outputs. Search for old numeric fingerprints and stale wording from the superseded run (e.g. old P95s, old timeout counts, "all timed out", "data contradiction") before finalizing.

If the replacement data changes only part of the report, recalculate global totals by subtracting the superseded rows and adding the new rows rather than recomputing from memory. For the passport-recognition report, replacing B rows changed totals to 1,492 requests, 4,121 images, 3,517 successful images, 99.53% HTTP success, and 85.34% image success.

## Executive summary for managers

For formal pressure-test reports, keep the executive summary concise enough for a manager to read on the first page. If the user says it is too long, compress it to **two short narrative paragraphs**:

1. API limits and safe usage: total test volume, proven usable range, hard boundaries, and concrete usage guidance.
2. Cost: total tokens, pricing assumption, conservative estimate, and per-image average.

Put detailed manager-facing guidance in a small table immediately below the summary, not in more paragraphs. A useful table is `建議 API 使用限制` with columns:

| 使用面向 | 建議限制 | 依據 / 原因 |
| --- | --- | --- |

Rows should include concrete measured limits when available:

- general synchronous cadence;
- short burst/high-peak rate;
- single synchronous batch size;
- large-batch handling (async/job/polling);
- long-duration stable cadence;
- QPS/resource saturation warning;
- input quality precheck;
- caller-side mechanisms such as timeout retry, idempotency, result lookup, and error classification.

Prefer wording such as: `建議其他團隊以「受控、低頻、小批量、可重試」方式使用 API` and then state concrete limits derived from measured data.

## Cost estimation when token sources are not split

If a spreadsheet has total token usage but does not split input / output / cached tokens, look up the model's current pricing and use the most expensive relevant token price as a conservative upper-bound estimate. State the assumption clearly.

If a later/replacement spreadsheet includes finer token columns for only some scenarios, use a mixed estimate:

1. For split rows, price input tokens at the input rate and output tokens at the output rate.
2. If `total_tokens > input_tokens + output_tokens`, treat the difference as `unclassified_tokens` and price it conservatively at the highest relevant token rate unless the user explains the bucket.
3. For unsplit rows from other scenarios, keep the conservative all-output-token estimate.
4. Present a cost table that separates unsplit tokens, split input tokens, split output tokens, unclassified tokens, and total estimated cost.

For Gemini 2.5 Flash Standard paid tier, the pricing observed during the passport-recognition report was:

- input text/image/video: US$0.30 / 1M tokens;
- input audio: US$1.00 / 1M tokens;
- output including thinking tokens: US$2.50 / 1M tokens;
- context caching text/image/video: US$0.03 / 1M tokens;
- context caching audio: US$0.10 / 1M tokens;
- cache storage: US$1.00 / 1M tokens / hour.

Because output was the highest per-token item, use `total_tokens / 1_000_000 * 2.50` for a conservative estimate unless the user provides a more specific token breakdown. Note separately that cache storage depends on retention time and may need separate calculation.
