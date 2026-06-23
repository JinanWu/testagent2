# Passenger-survey Gemini summary truncation investigation

Use when a dashboard summary ends mid-sentence even though the frontend/API/BigQuery paths appear structurally correct.

## Failure shape

A `summary_tree` node can be stored in BigQuery with a visibly incomplete sentence, for example ending with a fragment like `相較之下，關`. If the same text is present in the local Stage3 Gemini checkpoint cache and in BigQuery, the truncation happened during summary generation, not in frontend rendering or BigQuery serialization.

## Investigation pattern

1. Prove the layer where truncation appears.
   - Compare frontend text against the raw API payload.
   - Query the BigQuery snapshot row and extract `TO_JSON_STRING(summary_tree)` for the same period/run.
   - Inspect the local checkpoint cache, for example `scripts/.cache/*stage3_gemini_summary_cache.json`, for the same path key such as `json.dumps(["日本"], ensure_ascii=False, separators=(",", ":"))`.

2. Quantify whether it is systemic.
   - Walk all cached nodes with non-empty `summary`.
   - Count summaries whose final character is not a reasonable sentence terminator such as `。！？!?)）」』】》…`.
   - Break down by `kind` (`leaf` vs `branch`) and report the denominator and percentage.

3. Inspect the Gemini REST wrapper.
   - If the code only joins `candidate.content.parts[].text` and returns as soon as text is non-empty, it may accept partial output.
   - Require `candidate.finishReason == "STOP"` before accepting a summary.
   - Treat `MAX_TOKENS`, `SAFETY`, `RECITATION`, `OTHER`, missing finish reason, or no-text candidates as failed attempts requiring retry or escalation.

4. Check output budget assumptions.
   - Gemini 2.5 models may consume substantial `thoughtsTokenCount` within the output budget.
   - Even `maxOutputTokens=2048` can be insufficient when thinking tokens are high; a visible candidate can end mid-sentence.
   - Log `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`, `totalTokenCount`) and `finishReason` for every generated node.

## Remediation pattern

- Increase `maxOutputTokens` for semantic rollups, for example 4096 or 8192 when prompt size and model limits allow.
- Add a completeness gate: summaries must end with a sentence terminator and should not end on obvious connective fragments.
- Store per-node generation metadata in the cache (`finishReason`, token counts, model, attempt count, generated_at), not just the summary text.
- On reruns, do not blindly trust existing cache entries; scan and invalidate suspicious partial summaries before rebuilding.
- Before appending/replacing BigQuery snapshots, run a whole-tree quality gate: `finishReason == STOP` for all generated nodes, zero suspicious endings, non-empty root/top-level summaries, and recursive metrics/summary child-key parity.

## Root-cause phrasing

If the local cache and BigQuery both contain the same incomplete text, phrase the cause as: Gemini generation returned a partial candidate that the wrapper accepted because it did not validate `finishReason` or sentence completeness; the cache and BigQuery then preserved that partial text. Do not blame the frontend or BigQuery write path unless their raw payloads differ from the cache/source text.
