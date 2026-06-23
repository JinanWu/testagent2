# Presentation deck prompt and QA checklist

Use this checklist when asking Codex to build or revise an HTML presentation site.

## Copy handling
- Keep slide copy short and executive-friendly.
- Treat subtitles/captions as optional: if the text is empty, do not reserve layout space for it.
- Avoid strong conclusion wording in subtitles when the slide is meant to introduce context.
- Use neutral labels for sensitive comparisons, e.g. `現行方案 / 新方案`.

## Data management
- Put all editable numbers, labels, and slide copy in one obvious data file.
- If the site must run from `file://`, prefer a JS data module over fetch-based JSON.
- Keep theme tokens, radii, and shadows centralized.

## Responsive QA
Check at least these view sizes:
- 1366x768
- 1440x900
- 1280x800
- 1024x768
- narrow browser window
- tablet-ish width

For each slide, verify:
- title does not wrap into an unreadable block
- subtitle is hidden or collapses cleanly when empty
- charts stay inside the card
- nav and page counter do not overlap content
- no clipped content below the fold
- if height is short, the slide can scroll or scale instead of hard-clipping

## Print / export QA
- Add print styles if the deck is meant to be exported to PDF.
- Ensure all slides render as separate pages when printed.
- Disable motion and overflow clipping in print mode.

## Prompt wording that helps Codex
- "Please verify the deck on short-height screens, not only desktop 16:9."
- "Blank subtitles should not consume space."
- "If the viewport height is insufficient, allow scrolling or adaptive scaling rather than clipping content."
- "Run a final QA pass for overflow on titles, subtitles, charts, nav, and page counters."
