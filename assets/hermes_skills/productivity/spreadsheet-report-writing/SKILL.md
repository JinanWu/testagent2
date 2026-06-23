---
name: spreadsheet-report-writing
description: "Turn spreadsheet experiment/results files plus planning notes into structured Markdown reports."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [spreadsheets, reports, markdown, analysis, numbers, excel]
---

# Spreadsheet Report Writing

Use this skill when the user asks to produce a written report from spreadsheet results, especially when paired with an experiment plan, test plan, KPI table, or operational notes. Typical outputs include Markdown reports, executive summaries, test reports, validation reports, and comparison reports.

## Trigger examples

- "這是實驗結果，這是實驗文件，幫我寫報告"
- "用這份 Numbers / Excel / CSV 幫我整理成 md 報告"
- "把壓力測試結果寫成報告書"
- "compare the spreadsheet results against the test plan"

## Workflow

1. Confirm source paths and target path from the user request.
2. Read the plan/spec document first to understand expected experiments, metrics, pass criteria, and naming conventions.
3. Inspect the spreadsheet format:
   - `.csv` / `.tsv`: parse directly.
   - `.xlsx`: parse with Python (`openpyxl` when available) or another local tool.
   - `.numbers` on macOS: export to `.xlsx` using Numbers.app automation, then parse the exported workbook.
4. Extract the header row and validate the metric columns before writing conclusions.
5. Compute or verify aggregates from the spreadsheet, rather than relying only on visible summary cells.
6. Compare actual experiments against the plan:
   - completed vs not measured;
   - deviations from planned cadence/counts/time windows;
   - missing metrics or ambiguous rows.
7. Write the report with a clear structure:
   - executive summary;
   - overall statistics;
   - scope and plan/result mapping;
   - results table;
   - per-scenario analysis;
   - error/latency/cost analysis;
   - pass/fail or risk classification;
   - follow-up recommendations;
   - appendix with source data summary.
8. Explicitly flag data contradictions instead of smoothing them over, e.g. "image success rate is 100% but HTTP 200 count is 0".
9. Save to the user-requested path and verify file existence, line count/size, and a short readback sample before finalizing.

## Report-writing rules

- Keep conclusions tied to concrete numbers.
- Distinguish request-level success from item/image/row-level success when both are present.
- Do not infer unmeasured tests as passed. Default to mentioning unmeasured planned tests only if the user wants plan coverage / gap analysis; if the user says the report should focus on measured work or asks to remove untested plans, omit unmeasured plan rows and follow-up sections entirely.
- In per-scenario analysis, make `目的` explanatory, not one-line: include what the test is meant to reveal, why that scenario matters operationally, and what signals/thresholds to look for.
- In per-scenario `觀察`, prefer narrative paragraphs over bullet lists when writing formal reports; describe how the result changes across conditions and what it feels like operationally (stable, nearing boundary, cliff drop, recovery, etc.).
- In per-scenario `結論`, use a paragraph plus one explicit `重點：本測試測出的極限是...` sentence so the reader can quickly identify the measured boundary.
- If an item-level processing timeout exists (e.g. one passport image has a 45s limit), treat P95/max latency near or above that limit as a danger zone, not merely "slow". Call out that the tail is approaching the timeout threshold.
- If a spreadsheet omits cost but has token usage, present token usage as a cost proxy and state that monetary cost requires model pricing.
- If the user provides a replacement/retest spreadsheet because an earlier result was wrong or contradictory, treat the replacement as superseding the old rows: update every aggregate, table, narrative conclusion, appendix row, recommendation, and generated Word/HTML artifact; remove stale failure language from the old run.
- If a later spreadsheet has more detailed token splits than the earlier aggregate data, use the detailed split where available and keep a conservative assumption only for unsplit rows. Make the mixed estimation method explicit.
- If an experiment succeeded functionally but has high latency, mark it as "functionally passed but operationally risky" rather than simply passed.
- Preserve important plan/result differences, such as actual burst rates differing from the original plan, but do not over-emphasize unexecuted plan items when the report is meant to summarize results.

See `references/stress-test-report-style.md` for a concise pattern captured from a passport-recognition pressure-test report revision.

## macOS Numbers export pattern

When given a `.numbers` file and spreadsheet parsing is required, use Numbers.app to export to `.xlsx`, then parse the workbook. This may require user approval for application automation.

See `references/numbers-to-markdown-report.md` for a known-good pattern and pitfalls from a pressure-test report session.

## Word / Pages-compatible output

If the user asks for a Word report or Pages-openable report after a Markdown report is produced, keep the Markdown as source of truth, render it to simple HTML, then use macOS `textutil` to convert the HTML to `.docx`. Verify the `.docx` with `file`, `ls -lh`, and a short text readback. See `references/markdown-to-docx-pages.md` for the known-good pattern.

## Verification checklist

Before final response:

- Source plan was read.
- Spreadsheet data was parsed, not guessed.
- Aggregates were computed or checked.
- Output file path exists.
- Markdown was read back at least briefly.
- Final reply includes the exact output path and a short description of what was produced.
