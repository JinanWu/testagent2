# Markdown report to Word / Pages-compatible DOCX on macOS

Use when the user asks for a Word report or a file that Apple Pages can open after a Markdown report has been written.

## Recommended pattern

1. Keep the Markdown report as the source of truth.
2. Convert Markdown to simple styled HTML with headings, paragraphs, bullets, and tables.
3. Use macOS `textutil` to convert the HTML to `.docx`.
4. Verify the `.docx` with `file`, `ls -lh`, and a small text readback via `textutil -convert txt -stdout`.

This avoids requiring Pandoc and produces a `.docx` that Pages can open.

## Minimal conversion shape

```bash
# after writing report.md and report.html
textutil -convert docx -output /path/to/report.docx /path/to/report.html
file /path/to/report.docx
ls -lh /path/to/report.docx
textutil -convert txt -stdout /path/to/report.docx | head -n 30
```

## HTML rendering notes

- Include `<meta charset="utf-8">` so Chinese text survives conversion.
- Tables should be real HTML `<table><thead><tbody>` elements, not Markdown table text inside paragraphs.
- Add basic CSS for fonts and table borders. `textutil` preserves enough styling for readable Word/Pages output.
- Very wide tables may be less readable in Word; keep executive-summary recommendation tables concise.

## User-facing wording

Tell the user both paths:

- `.docx` for Word / Pages: portable and easy to share.
- `.html` as an intermediate preview / fallback.

If the user specifically asks for Pages-native `.pages`, prefer giving `.docx` first unless they insist; Pages opens `.docx` reliably, while creating native `.pages` programmatically is more brittle.
