# API-first drill-down diagnosis for dashboards

Session-derived notes for diagnosing dashboards that stop after a few clicks.

## Core rule

When a live drill-down dashboard appears to stop at a shallow level, compare the UI depth against the API depth before treating it as a click-handler bug.

## Checklist

1. Inspect the live hierarchy payload.
   - Confirm whether deeper child arrays already exist (for example: `product -> tours -> guests`).
   - If the API has the deeper nodes, the missing depth is usually frontend wiring, fallback data, or a static subpage.

2. Check whether the page uses fallback/mock content.
   - A page may render a convincing hierarchy shell while actually showing placeholder data.
   - A static tracking/detail subpage can look interactive even when it never fetches the backend.

3. Check for late-arriving data handling.
   - If the data may appear after ingestion lag, verify whether the UI retries, polls, or refetches.
   - If it only fetches once on mount, waiting longer will not help.

4. If the data layer is missing a level, look for a stable grouping key in the raw facts.
   - In this session, `tour_code` was sufficient to materialize a missing `tour` level under `product`.
   - For nested snapshots, serialize JSON/tree fields explicitly if the insert API expects strings.

## Outcome pattern from this session

- API response contained the full hierarchy.
- The main dashboard page could render the deeper shape, but only if it received the real payload.
- The tracking subpage was static mock content, not a live drill-down.
- The frontend had no retry/polling, so it could not recover after the first failed fetch.
