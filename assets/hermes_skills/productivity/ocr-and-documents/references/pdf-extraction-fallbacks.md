# PDF extraction fallback notes

Use this when the goal is fast reading of a text-based PDF and full OCR/layout extraction is unnecessary.

## Minimal fallback
- Tooling: `pypdf`
- Install: `pip install pypdf`
- Extract text page-by-page:
  - `reader = PdfReader(path)`
  - `for page in reader.pages: text = page.extract_text() or ''`
- When the user asks about a specific concept, search pages first for the keyword, then read the matching page(s) in full.

## Good use cases
- You need a quick answer from a normal digital PDF
- The document is long and you want to locate a topic like a section title, then inspect the page text around it
- You do not need OCR for scanned pages, equations, or layout preservation

## Reminder
- Use full OCR/layout tools only when the PDF is scanned or structure matters.
- For short conversation support, quote the relevant page and explain it in plain language.
