# Numbers spreadsheet to Markdown report pattern

Session pattern captured from a passport-recognition pressure-test report.

## Problem shape

The user provided:

- a `.numbers` spreadsheet containing experiment results;
- a Markdown test plan describing intended scenarios and shared metrics;
- a target directory for a generated `.md` report.

The successful workflow was:

1. Read the test plan first.
2. Export the `.numbers` file to `.xlsx` with Numbers.app.
3. Parse the exported workbook with Python/openpyxl.
4. Build aggregate metrics and a row-level result table.
5. Write a structured Markdown report.
6. Verify file line count, size, and readback sample.

## Export command pattern

On macOS, `.numbers` files are ZIP bundles but are easiest to parse by exporting with Numbers.app:

```bash
mkdir -p /tmp/stress_numbers_export && rm -f /tmp/stress_numbers_export/stress_test.xlsx
osascript <<'APPLESCRIPT'
set inputFile to POSIX file "/path/to/input.numbers"
set outputFile to POSIX file "/tmp/stress_numbers_export/output.xlsx"
tell application "Numbers"
    open inputFile
    delay 2
    set theDoc to front document
    export theDoc to outputFile as Microsoft Excel
    close theDoc saving no
end tell
APPLESCRIPT
file /tmp/stress_numbers_export/output.xlsx
```

Notes:

- This may trigger an automation approval prompt. If the user misses or denies it, stop and ask them to approve before retrying.
- Do not conclude the file is unreadable just because direct parsing of `.numbers` is inconvenient.
- Export to `/tmp` or another scratch directory; do not overwrite the user's original file.

## Workbook parsing pattern

```bash
python3 - <<'PY'
import openpyxl, json
path = '/tmp/stress_numbers_export/output.xlsx'
wb = openpyxl.load_workbook(path, data_only=True)
print('sheets:', wb.sheetnames)
for ws in wb.worksheets:
    print('\n##', ws.title, ws.max_row, ws.max_column)
    for row in ws.iter_rows(values_only=True):
        if any(v is not None for v in row):
            print('\t'.join('' if v is None else str(v) for v in row))
PY
```

If `execute_code` has a different Python environment, use `terminal` with `python3` for package availability checks and parsing.

## Analysis rules used in the report

- Compute request-level and image/item-level totals separately.
- Treat HTTP 200/request success and image/item success as different dimensions.
- If item success is 100% but HTTP success is 0%/5xx, inspect semantics before calling it a contradiction. In pressure-test APIs, this can mean all items were processed successfully but the synchronous API timed out before returning a response. Report it as "backend completed, API did not successfully respond" and recommend async job/result polling if confirmed.
- Mark high-success but very slow tests as operationally risky, not clean passes.
- Compare actual tested scenarios to the plan; identify untested scenarios only when the user wants coverage/gap analysis.
- If the user asks to remove untested plans, omit those rows/sections and recompute aggregates.
- If token data exists without token-source breakdown, look up the current model pricing and use the most expensive relevant token unit as a conservative upper-bound estimate.

## Recommended Markdown sections

1. Title and data sources.
2. Executive summary.
3. Overall statistics table.
4. Plan vs result coverage table.
5. Full results summary table.
6. Per-scenario analysis.
7. Error and abnormality analysis.
8. Latency/performance analysis.
9. Output consistency or quality analysis, if relevant.
10. Cost/token analysis.
11. Pass/fail/risk classification.
12. Follow-up recommendations.
13. Final judgment.
14. Appendix with raw row summary.

## Verification

After writing the file:

```bash
wc -l /target/report.md
ls -lh /target/report.md
```

Also read back the first section to ensure the report was written to the correct file and is not empty or malformed.
