---
name: enterprise-ai-platform-architecture
description: "Plan enterprise AI Agent platforms: Hermes-like runtimes, Cloud Run microservices, contract-first APIs, governance, RAG, audit, and local Docker simulation."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ai-platform, architecture, cloud-run, microservices, contracts, agent-runtime, governance, rag]
    related_skills: [hermes-agent, writing-plans, api-operations, observability]
---

# Enterprise AI Platform Architecture

## When to use

Use this skill when the user is designing or implementing an internal AI platform, AI governance portal, Hermes-like Agent runtime, multi-Agent tool platform, or enterprise chatbot that must include permissions, audit, RAG, citations, cost tracking, sensitive-data handling, and deployable service boundaries.

Strong triggers:
- The user wants to build a "private Hermes", "internal AI platform", or "Agent platform".
- The platform will run on GCP Cloud Run or similar container/serverless infrastructure.
- The user asks for module boundaries, API contracts, JSON schemas, TypeScript interfaces, or microservice splits before implementation.
- The system must support Feature / Skill / Tool governance instead of a simple chatbot.
- The user emphasizes diagrams → boundaries → contracts before parallel development.

## Core principle

Do not start by scaffolding code. Start by freezing the platform flow and the contracts that allow parallel development. Treat the chatbot UI as an entry point, not the platform itself.

Recommended positioning:

> Internal AI Agent Runtime + Tool Governance Platform

The user-facing product may look like a chatbot, but the architecture is an Agent runtime with governed Feature / Skill / Tool / Knowledge / Permission / Audit layers.

## Reference architecture shape

For a Cloud Run-first implementation, prefer this initial service split:

1. `web-app`
   - Frontend UI: feature menu, chat, file upload, citations, sensitive warnings, admin entry.
2. `api-gateway`
   - Public API, auth middleware, session/message API, SSE event stream, file upload proxy, admin query API.
   - It should not contain LLM/tool-loop logic.
3. `agent-runtime`
   - Hermes-like core: Skill Router, Prompt Builder, Model Router, Tool Executor, tool-call loop, run state machine, RunEvent producer.
4. `registry-service`
   - Feature registry, Skill registry, Tool registry, capability bundles, allowed tools, skill versions.
5. `policy-service`
   - UserContext, feature/skill/tool/knowledge permission checks, future SSO/IAM integration.
6. `knowledge-service`
   - File metadata, Cloud Storage to Markdown integration, chunking, embeddings, vector retrieval, citation builder, internal/external source separation.
7. `governance-service`
   - Audit events, sensitive detection, usage/cost tracking, error events, admin reporting.

Shared infrastructure:
- CloudSQL / PostgreSQL
- Cloud Storage
- Vector index, preferably `pgvector` for MVP before Vertex AI Vector Search
- Secret Manager
- Artifact Registry
- Cloud Run
- Local Docker Compose to simulate deployment boundaries

## Avoid over-splitting in MVP

When the user feels overwhelmed by the full platform size, actively shrink the scope before continuing. Recommend a **modular monolith first**: keep module boundaries and contracts explicit in the repo, but deploy only a small vertical slice such as `web-app`, `api-agent-service`, and `postgres` until the runtime spine works.

Do not initially split these into standalone Cloud Run services unless there is a proven scaling/security need:
- model router
- prompt builder
- tool executor
- sensitive detector
- citation builder
- cost tracker

Keep them inside `agent-runtime` / `api-agent-service` or `governance-service` for MVP. Splitting too early increases latency, contract churn, deployment overhead, and debugging difficulty.

Use this MVP-0 scope when the architecture feels too large:

```text
Feature / Skill -> Prompt -> Model -> optional Tool -> Event -> Audit -> Final Answer
```

Stub complex subsystems while preserving contracts:
- policy check can return `allow` for demo users;
- sensitive detection can use regex warn-only;
- knowledge search can return a mock citation;
- feature/skill registry can start as YAML/JSON;
- audit should still write real session/message/run/tool records.

## Frontend-first planning artifact

When the user asks to start from frontend technology or asks for a screen that decomposes component interactions, produce a verified local HTML artifact before implementation planning. The artifact should show the product surface and backend boundaries together: left feature menu, center layered interaction map, and right inspector panel with components, contracts, and one-message event flow. Use this as a bridge from diagrams to actual project scaffolding, especially for internal AI platform planning where the frontend must render RunEvents, citations, sensitive warnings, and admin/governance surfaces.

See `references/frontend-interaction-map-artifacts.md` for a proven artifact shape, contract labels, layer filters, build sequence, and visual verification checklist.

## Contract-first sequence

Before coding, define these contracts in JSON Schema / OpenAPI / TypeScript interfaces:

1. `UserContext`
   - user id, account, department, roles, feature permissions, data scopes.
2. `FeatureContract`
   - left-menu product feature, display metadata, default skill, enablement, permission requirements.
3. `SkillContract`
   - skill id/version, prompt template, input/output schemas, allowed tools, sensitive policy, audit policy.
4. `ToolContract`
   - tool name, input/output schemas, risk level, timeout, approval requirement, permission scope.
5. `RunRequest`
   - API Gateway → Agent Runtime execution request.
6. `RunResponse`
   - final state, answer, citations, warnings, errors, usage summary.
7. `RunEvent`
   - event-stream envelope for SSE/WebSocket and audit mirroring.
8. `MessageContract`
   - user/assistant/tool messages, full content, masked content, citations, sensitive hits, usage.
9. `ToolCallContract`
   - LLM tool call, tool args, policy decision, tool result, error details.
10. `PolicyDecision`
    - allow / deny / warn / require_approval, reasons, matched rules.
11. `SensitiveHitContract`
    - type, span/location, masked value, severity, action; MVP often uses warn-only.
12. `RetrievalRequest` / `RetrievalResult`
    - query, knowledge scopes, filters, top_k, chunks, scores.
13. `CitationContract`
    - document/chunk ids, file name, page, paragraph, Excel sheet/cell range, snippet, score.
14. `AuditEventContract` / `CostContract`
    - actor, run, event type, timestamps, tokens, model, provider, estimated cost.

## Main runtime flow

Use this canonical MVP flow:

1. User logs in.
2. API Gateway resolves `UserContext`.
3. Frontend loads allowed `FeatureContract` list.
4. User selects a feature or types freely.
5. Frontend sends message request.
6. API Gateway creates Session / Message / Run.
7. Agent Runtime receives `RunRequest`.
8. Runtime resolves Skill from selected feature or intent.
9. Policy Service checks Feature / Skill / Knowledge / Tool scopes.
10. Sensitive detection checks user input.
11. Prompt Builder combines system prompt, skill prompt, user context, allowed tools, and optional RAG context.
12. Model Router calls the LLM.
13. If the LLM returns a tool call, Runtime performs tool policy check, executes the tool, records result and citations, then feeds the tool result back to the LLM.
14. Runtime generates final answer.
15. Sensitive detection checks output.
16. Governance records messages, run events, tool calls, citations, sensitive hits, usage/cost, errors.
17. API Gateway streams RunEvents to the frontend.
18. Frontend renders answer, citations, tool progress, warnings, and errors.

## Required RunEvent types

For a web-first Agent UI, define these early:

- `run.started`
- `message.received`
- `skill.selecting`
- `skill.selected`
- `permission.checking`
- `permission.allowed`
- `permission.denied`
- `sensitive.detected`
- `prompt.built`
- `model.started`
- `model.delta`
- `model.completed`
- `tool.call.requested`
- `tool.permission.checking`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `rag.search.started`
- `rag.search.completed`
- `citation.attached`
- `message.completed`
- `run.completed`
- `run.failed`
- `run.cancelled`

## Data-flow rule

Separate two flows:

1. Conversation execution flow
   - user message → Agent Runtime → LLM → tool/RAG → answer → citations/events.
2. Governance record flow
   - audit events, token usage, cost, sensitive hits, permission decisions, tool calls, errors.

Do not make governance depend only on final chat output. It must be queryable independently by admin/reporting surfaces.

## Local and Cloud deployment guidance

Recommended repo shape:

```text
ai-platform/
  apps/
    web-app/
  services/
    api-gateway/
    agent-runtime/
    registry-service/
    policy-service/
    knowledge-service/
    governance-service/
  packages/
    contracts/
    shared/
  infra/
    docker-compose.yml
    cloudrun/
    terraform/
  docs/
    architecture/
    contracts/
    flows/
```

Local development:
- `docker-compose up`
- one container per service boundary
- shared PostgreSQL
- optional MinIO or Cloud Storage emulator
- mock LLM provider for deterministic tests
- mock Cloud Storage → Markdown adapter until the real converter is integrated

Cloud Run:
- `api-gateway` exposed publicly
- internal services use internal ingress where possible
- secrets via Secret Manager
- DB via CloudSQL
- files via Cloud Storage
- images via Artifact Registry

## Hermes comparison guidance

When referencing Hermes, map concepts rather than copying everything. For the detailed Hermes Agent Runtime code-walk and reduced MVP runtime spine, see `references/hermes-agent-runtime-flow.md`.

When the user wants to implement the runtime spine first, especially as a Traditional Chinese code readability experiment, use `references/traditional-chinese-agentruntime-prototype.md`. It captures the minimal Hermes-like loop, Chinese naming/docstring conventions, SQLite tables, fake-model tests, and verification checklist.

When the user wants an actual web chat scaffold for a Hermes-like platform, use `references/traditional-chinese-web-agent-scaffold.md`. It captures the proven FastAPI + static frontend vertical slice, Gateway -> Runtime handoff, safe "Hermes tools built-in as catalog first" approach, Gemini ADC verification pattern, and end-to-end fake-model checks.

When the user asks for project-owned Python code to be readable to Chinese users, or asks to initialize/push a generated Hermes-like project to git, use `references/hermes-like-project-git-and-chinese-code-readability.md`. It captures the user's Traditional Chinese coding preference, the boundary between project-owned Chinese names and external English API contracts, post-rename verification, and the `.gitignore`/first-commit checklist for aiagent-style projects.

When designing the API Gateway → Agent Runtime boundary, use `references/gateway-runtime-context-handoff.md`. It captures the Hermes-informed lesson that Gateway should hand off a standardized execution envelope: current message, conversation history or history-loading instructions, user/session/source context, selected feature/skill hint, execution options, governance requirements, response-channel metadata, and trace ids. Keep Gateway responsible for normalization/delivery and Runtime responsible for skill routing, prompt building, model/tool/RAG execution, and RunEvent emission.

When the user says the prototype is too shallow and asks to copy/migrate Hermes prompts, tools, skills, or full behavior, use `references/hermes-compatibility-layer.md`. Prefer a Hermes compatibility bridge (source loader + prompt/tool/skills/full-agent delegation) over hand-copying the entire Hermes repo.

When implementing a Hermes-like web Agent Runtime and the user notices tools are catalog-only, web search cannot run, or the agent stops after saying "I will do X", use `references/hermes-like-web-agent-runtime-continuation.md`. It captures the durable pattern for executable Hermes tool bridging, tool-use/finish-the-job prompt guidance, response classification, post-tool continuation, finish-reason handling, SSE event streaming, and still-working heartbeats.

When a Hermes-like web prototype has copied tool names or a tool manifest but the agent cannot actually perform actions such as web search, terminal, file, browser, skills, memory, or cron, use `references/hermes-tool-compatibility-and-continuation.md`. The durable lesson is: tool catalog is not execution. Bridge to Hermes `model_tools.get_tool_definitions` + `handle_function_call` or implement real handlers, expose only tools that pass availability checks, and add a continuation controller so the runtime does not stop at intermediate acknowledgments, empty post-tool responses, or truncated output.

When the user specifically wants Hermes skills/tools to be operational inside a new web Agent project, use `references/hermes-tool-compatibility-bridge.md`. It captures the proven pattern: load Hermes `model_tools.get_tool_definitions(...)`, register only tools that pass Hermes `check_fn`, delegate execution to `handle_function_call(...)`, expose executable tools (not catalog-only names) to the model, install/configure a web backend such as `ddgs` when needed, and verify with live `web_search` plus `terminal` smoke tests.

Useful Hermes ideas:
- Agent loop / tool calling
- Skill loading
- tool registry
- model routing
- session and message persistence
- event streaming
- cost/audit tracking
- permissions and safety guardrails

Implementation pitfall from Hermes-like prototypes:
- Do not describe Hermes tools as “built in” unless they are actually executable by the new runtime. A copied tool catalog/manifest is only discoverability metadata; it is not a registered handler. Explicitly distinguish copied catalog entries from model-exposed tool schemas and from handlers wired to policy, async execution, result limits, and audit.
- If the user expects web research, implement or bridge `web_search` / `web_extract` early and verify with a real `/api/chat` request that the model sees those tool schemas and calls them. Otherwise the model will correctly report that it cannot browse even if `web_search` exists in the copied Hermes catalog.

Avoid copying directly for an enterprise web platform:
- CLI-first assumptions
- local filesystem and terminal tool assumptions
- profiles/plugins complexity in MVP
- messaging gateway adapter breadth
- agent-managed external communication channels

## Common pitfalls

1. Calling it only a chatbot.
   - This hides governance, permission, audit, and tool registry needs.
2. Coding before contracts.
   - Parallel development fails if Feature / Skill / Tool / RunEvent contracts are not frozen first.
3. Over-splitting microservices.
   - Keep high-chatter internals together until the domain stabilizes.
4. Mixing feature permission with data permission.
   - A user may access "knowledge Q&A" but only a subset of knowledge bases.
5. Losing source traceability.
   - Citations must preserve page/paragraph and Excel sheet/cell metadata where applicable.
6. Treating sensitive detection as blocking by default.
   - For many MVPs, start with warn-and-record while designing escalation policy.
7. Letting frontend call LLM/tools directly.
   - All usage must go through API Gateway and Runtime so governance can observe it.

## Session reference

See `references/cloudrun-hermes-like-contract-first.md` for a condensed example derived from a Hermes-like internal AI platform planning session.