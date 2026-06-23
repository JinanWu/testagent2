# Text Truncation Root-Cause Checklist

Use this when a dashboard or UI appears to cut a sentence in the middle.

## First question: where is the truncation happening?
Check all four layers before fixing anything:
1. Source generation: did the backend already shorten the text?
2. API/serialization: did the payload drop or transform the text?
3. Storage/snapshot: is the saved record already truncated?
4. Frontend render: is the UI applying a hard slice, clamp, or overflow rule?

## Evidence to collect
- Exact sample text that appears truncated
- Raw API response for the same record
- Stored snapshot / database row if available
- UI component code around the render path
- CSS/layout rules that can visually clip text

## Common pitfall
A text block can be truncated twice:
- once by a hard string operation such as `slice(0, N)`
- again by CSS such as `overflow-hidden`, `line-clamp`, or a fixed-height card

If the displayed text ends with an ellipsis but the raw payload is longer, suspect frontend preview logic first.

## Verification order
1. Compare raw data vs rendered text.
2. Search the component tree for hard substring operations.
3. Check whether the expand/detail view reuses the same truncated variable.
4. Only then change the rendering logic or upstream generation.