# Dashboard hierarchy monthly snapshot verification

Use this when a dashboard hierarchy / tree is sourced from BigQuery snapshot data and you need to inspect a specific month (e.g. May) and split the result by top-level category.

## Pattern
1. Call the real API endpoint that serves the tree, not the UI bundle.
2. Send explicit JSON headers and a browser-like User-Agent when the endpoint sits behind Cloudflare / edge protection.
3. Save the response to a file first, then parse it with a separate step. Avoid piping the download directly into the parser.
4. Extract:
   - source metadata: runId, runTs, tourDateStart, tourDateEnd
   - top-level category list and counts
   - each category’s direct children and their opinion counts
5. If the tree is nested, inspect one representative branch, one representative leaf, and one zero-count branch.
6. Report the month window explicitly, because snapshot endpoints often serve “latest month” rather than a query-time live range.

## Example probe
```bash
curl -sS -A 'Mozilla/5.0' -H 'Accept: application/json' \
  'https://<host>/dashboard/api/v1/satisfaction/hierarchy' \
  -o /tmp/hierarchy.json

python3 - <<'PY' /tmp/hierarchy.json
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    data = json.load(f)
print(data['source'])
for region in data.get('regions', []):
    print(region['name'], region.get('opinionCount'), region.get('scoredCount'))
    print([child['name'] for child in region.get('lines', [])])
PY
```

## Pitfalls
- Do not assume every top-level category has deeper descendants; some branches are true leaves.
- Do not infer grouping logic from the UI alone; confirm the tree JSON shape first.
- Do not trust a single sample node to represent the whole month; split by top-level category and inspect the counts.
- If the response is protected, retry with a browser-like User-Agent before escalating to code changes.
