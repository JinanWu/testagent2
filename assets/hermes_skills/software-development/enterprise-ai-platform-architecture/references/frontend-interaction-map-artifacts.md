# Frontend-first interaction map artifact for enterprise AI platforms

Use when the user asks to start AI platform planning from the frontend or asks for a screen that decomposes component interactions before project scaffolding.

## Proven artifact shape

Create a self-contained local HTML file that shows the platform from the frontend outward:

1. Left product navigation
   - Feature menu such as free chat, knowledge Q&A, self-built agents, governance/admin.
   - Keep labels neutral and product-like, not implementation-only.
2. Center interaction map
   - Layered architecture nodes: Frontend, API Gateway, RunEvent/SSE, Registry, Agent Runtime, LLM/Embedding, Policy, Governance, RAG/Knowledge, Storage, Admin.
   - Include clickable nodes and layer filters so the user can inspect one slice at a time.
3. Right inspector panel
   - For the selected node, show:
     - frontend components to build;
     - contracts to freeze first;
     - one-message event path;
     - project-building sequence.
4. Bottom or side legend
   - Distinguish frontend/API, execution, governance, and data/knowledge layers.

## Recommended layer filters

- All interactions
- 1. Frontend screen
- 2. Execution flow
- 3. Governance and permissions
- 4. Knowledge and data
- 5. Build sequence

## Contracts to expose in the artifact

Show short contract names rather than full schemas at this stage:

- `GET /features`
- `POST /sessions`
- `POST /sessions/{id}/messages`
- `GET /runs/{run_id}/events` for SSE
- `POST /files/upload`
- `FeatureContract`, `SkillContract`, `ToolContract`
- `RunRequest`, `RunResponse`, `RunEvent`
- `PolicyDecision`, `UserContext`
- `AuditEvent`, `SensitiveHit`, `Cost`
- `RetrievalRequest`, `RetrievalResult`, `Citation`

## Frontend-first build sequence

1. App shell: left menu, right chat, history, admin entry.
2. Contracts: TypeScript types plus OpenAPI/mock data.
3. Event stream: SSE state machine and message renderer.
4. Agent panel: tool progress, citations, sensitive warnings.
5. Admin MVP: audit, usage/cost, sensitive-hit lookup.

## Verification checklist

- Open the local HTML in the browser.
- Check browser console for JS errors.
- Click at least one layer tab and one node.
- Use a screenshot/vision pass to check all nodes are visible; architecture maps often visually truncate rightmost or bottom nodes even when the DOM is valid.
- If nodes are clipped, reduce node width, rebalance coordinates, or increase the map canvas before finalizing.