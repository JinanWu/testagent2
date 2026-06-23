# Frontend UI verification checklist

Use this when a dashboard/UI bug is about scrollbars, overlay layout, or transparent canvas/WebGL visuals.

## Scrollbar / overflow issues
- Inspect the whole ancestor chain, not just the scrolling element.
- Confirm the scroll container has a bounded height from its parent.
- Check for missing `flex` / `flex-col` / `min-h-0` on the parent that is supposed to constrain the child.
- Compare `scrollHeight` vs `clientHeight` in the browser, not just the class names.
- If an absolute overlay contains an inner `flex-1 overflow-y-auto` region, make sure the overlay itself is a flex container or otherwise gives the child a real height context.
- Concrete dashboard-card pitfall: an `absolute inset-0` wrapper around a card list with an inner `flex-1 overflow-y-auto` can make the scrollbar appear inert because `flex-1` has no flex parent. Fix the wrapper with `flex min-h-0 overflow-hidden`, and give the inner scroller `h-full min-h-0 overflow-y-auto`.
- To verify independent of live data volume, temporarily append a tall filler element in DevTools, set `scrollTop`, and assert it changes (for example `clientHeight < scrollHeight` and `scrollTop > 0`). Remove the filler immediately after the probe.

## Transparent globe / canvas issues
- Verify the actual paint source for the non-land area: base sphere, clear color, background fill, stroke, or gradient.
- If the requirement is “only land is visible,” background-related paint instructions should usually be fully transparent (`rgba(0,0,0,0)`) or omitted.
- A transparent land mask does not make the underlying sphere transparent.
- Check both the code and the rendered pixels; class names alone are not enough.
- For animated canvas markers, test the real hitbox and event flow in-browser. A marker can be visible but hard to click if the globe keeps rotating between hover and click, or if the click target covers only the dot while users click the label. Prefer a shared `markerContainsPoint()` helper for hover and click, include both dot and label bounds, and pause/slow auto-rotation while `hoveredId` is set.
- When validating globe clicks, do not rely on one guessed coordinate. Scan the canvas for cursor changes or inspect marker hit data, then click a detected hit and assert the dashboard title/breadcrumb changes to the expected region.

## Dashboard scrollbar styling
- Global dark-mode scrollbar styles can make light dashboard card lists show a black/dark thumb. For light card panes, add a local class (for example `dashboard-card-scrollbar`) rather than changing the global scrollbar: use a light transparent track plus a medium gray thumb that darkens on hover.
- Verify the local class is on the actual overflow element, not its parent wrapper, and check `getComputedStyle(scroller).scrollbarColor` plus `clientHeight < scrollHeight` in the browser.
- For canvas globes with clickable markers, inspect hit-testing separately from rendering: cursor hover can work while click misses if the marker moves between hover and click. Use one shared hit-test helper for hover and click.
- If markers auto-rotate, pause rotation while a marker is hovered so the target does not drift away under the user’s pointer.
- Include both the marker circle and its label pill in the hitbox when the UI visually presents both as the clickable target.
- For rotating canvas globes with clickable markers, do not judge clickability from marker drawing alone. Probe the hit-test separately: scan for cursor changes or inspect the marker hit array, then dispatch a click at a verified hit point and wait for the drill-down state to change.
- If markers are hard to click because the globe auto-rotates, pause rotation while hovering a marker and make hover/click share the same hit-test helper. Include both marker circles and visible label pills in the hit area so users can click what they visually perceive as the target.

## Evidence to collect
- Screenshot of the rendered state.
- Relevant computed styles / drawing settings.
- Small code excerpt showing the element that creates the visual background.
- If needed, a minimal reproduction in the browser devtools console: `scrollHeight`, `clientHeight`, and the computed `display` / `overflow` chain.
- For branch-sensitive UI fixes, record `git branch --show-current` and `git status --short` before editing, then repeat after the fix so work is not accidentally done on the wrong starting branch.
