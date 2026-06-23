# Dashboard UI fallback-data verification

Use this pattern when a dashboard/tree/globe UI looks broken but may actually be starved of data.

## Symptom
- Globe markers or tree cards are visible, but clicks do nothing.
- A deeper layer never renders, or scroll containers appear empty.
- UI only becomes interactive after live API data arrives.

## What to check
1. Find the interaction gate:
   - `interactive`, `disabled`, `canDrill`, `hasLiveData`, `isLoaded`, or similar flags.
2. Find the fallback branch:
   - `data ?? FALLBACK_*`, `empty state`, or mocked demo data.
3. Compare render vs. click logic:
   - Render path may use fallback data while click path still requires live data.
4. Verify the data shape, not just presence:
   - Empty arrays at the top level can make the UI look valid while still producing zero clickable targets.

## Fix pattern
- Provide a minimal nested fallback dataset that includes at least one clickable path.
- Base the interactive gate on the actual rendered source data, not only on whether the live fetch succeeded.
- Keep the mock dataset small but deep enough to exercise the intended drill-down/scroll path.

## Verification
- Confirm the UI can enter the second layer without backend dependencies.
- Confirm at least one branch has children and one branch can reach a deeper level.
- Confirm the scrollable container actually contains enough items to overflow.
