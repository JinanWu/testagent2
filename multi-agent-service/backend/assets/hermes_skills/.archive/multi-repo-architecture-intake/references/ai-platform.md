# AI platform / multi-agent-service intake

This note captures the mixed architecture discussed for the AI platform project.

## Repos
- `multi-agent-service`: FastAPI backend for run orchestration, SSE event streaming, auth, tool-calling, and artifact persistence.
- `multi-agent-web`: Vite/React frontend for login, run creation, run detail streaming, and dashboard UI.

## What the AI question flow does today
- User submits text and/or images from the frontend.
- Backend creates a `Run` and executes an agent loop using Gemini.
- Execution is observable through `RunEvent` records and SSE, with polling fallback if SSE fails.
- Current configured agents are minimal: `root` and `receipt_extractor`.
- Current tools are minimal: `delegate_to_agent`, `save_json_result`, `save_text_result`.

## “Parasitic service” / co-located product boundary
- The backend mounts a separate dashboard backend under `/dashboard`.
- That dashboard backend is not the same as the AI agent UI; it serves a different product surface.
- The dashboard contract explicitly says the satisfaction hierarchy and opinions APIs are real backend-backed surfaces, while the “不滿追蹤” page is still a static front-end demo.

## Practical inspection cues
- When a repo seems to host multiple products, inspect:
  1. main app entrypoint / mounted subapps
  2. route contract docs
  3. frontend pages to see which ones are real vs static/demo
  4. config files for agent definitions or feature flags
- Don’t assume the repo name equals one product; verify the mounted routes and current UI entry points.

## Common pitfalls
- Mixing the AI task runner and dashboard subsystem into one generic summary.
- Assuming all dashboard pages are backed by the same live API.
- Missing the fact that the current agent set may be intentionally tiny even if the platform is broader.
