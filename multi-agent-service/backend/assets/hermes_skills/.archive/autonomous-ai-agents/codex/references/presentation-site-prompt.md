# HTML / front-end presentation prompt pattern

Use this when asking Codex to build a browser-openable presentation site.

## Prompt ingredients
- Repository path and target branch/state.
- Audience and goal: who will read it and what decision it should support.
- Narrative constraints: e.g. low political risk, use neutral labels like `現行方案 / 新方案`.
- Visual direction: warm/light palette, rounded cards, subtle gradients, soft shadows, small animations.
- Page structure: slide count and the point of each slide.
- Data management: put all editable numbers/text in one centralized data file.
- UX rules: short copy, readable charts, keyboard/mouse navigation, responsive layout.
- Deliverables: README, run instructions, and a verification checklist.

## Good request shape
- Tell Codex exactly where the repo lives.
- Say whether it should create a static site, Vite app, or single-file HTML.
- Specify the single source of truth for data.
- Tell it to leave the theme tokens and chart config in one obvious place.
- Ask for a final QA pass that checks layout, copy length, and chart correctness.

## Example checklist
- Can the site run locally with a simple command?
- Can a non-technical reviewer understand the first screen in 10 seconds?
- Are all numbers editable from one file?
- Are slide titles and labels short enough for executive review?
- Does the deck avoid direct competitor naming when the topic is sensitive?
