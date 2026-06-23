# Lightweight TypeScript Helper Regression Tests

Use this when a React/Vite project has no dedicated test runner (no Vitest/Jest) but a small UI behavior can be isolated into a pure TypeScript helper.

## Pattern

1. Extract the behavior into a pure `.ts` helper near the component.
   - Keep DOM/React concerns in the component.
   - Put deterministic rules in exported functions, e.g. score formatting, missing-value detection, sort rank calculation.
2. Add a tiny Node `.mjs` test script under `scripts/` using `node:assert/strict`.
3. Compile only the helper into a temporary directory with `tsc`, then import the emitted `.js` from the `.mjs` test.
4. Add an npm script that cleans the temp directory before and after the test.
5. Still run the normal `npm run build` and `npm run lint` after the focused regression test.

## Example package script

```json
{
  "scripts": {
    "test:dashboard-metrics": "rm -rf .tmp-dashboard-tests && tsc --outDir .tmp-dashboard-tests --module NodeNext --moduleResolution NodeNext --target ES2020 --strict src/components/dashboard/analyticsMetrics.ts && node scripts/test-dashboard-metric-display.mjs && rm -rf .tmp-dashboard-tests"
  }
}
```

## Example test script

```js
import assert from 'node:assert/strict'
import { formatDashboardMetricValue, sortDrillItemsByPassengerScore } from '../.tmp-dashboard-tests/analyticsMetrics.js'

assert.equal(formatDashboardMetricValue(0).label, '-')
assert.equal(formatDashboardMetricValue(0).isMissing, true)

const sorted = sortDrillItemsByPassengerScore([
  { id: 'missing', metrics: { passenger: 0 } },
  { id: 'normal', metrics: { passenger: 82 } },
])
assert.deepEqual(sorted.map(item => item.id), ['normal', 'missing'])
```

## Pitfalls

- If `tsc --outDir` is invoked with a single source file, the emitted file may land directly at `.tmp/.../helper.js`, not under the full `src/...` path. Inspect the emitted path once and make the `.mjs` import match it.
- Do not leave the temporary compile directory tracked or visible in git status; clean it in the npm script.
- Treat lint warnings separately: if they pre-exist and are unrelated, report them as existing warnings rather than bundling cleanup into the bug fix.
