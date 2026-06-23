# Cloud Run Hermes-like Contract-First Planning Reference

## Session context

The user wants to build an internal AI platform with Hermes-like capabilities but deployed as a Cloud Run / Docker-first web platform. The platform is for all employees, aims to reduce shadow AI, and should govern self-developed Agent functions through permissions, data scopes, citations, audit, cost tracking, and sensitive-data warnings.

The source materials in the session were:
- `/Users/wujinan/Documents/hermes-agent` as a reference implementation for Agent runtime concepts.
- `/Users/wujinan/Documents/ai平台架構圖/` containing architecture, module boundary, contract map, runtime flow, Feature/Skill/Tool, RAG/citation, audit/sensitive/cost, development sequence, and summary boundary map docs.

## Important interpretation

The correct architecture is not a clone of Hermes. It is a Web/API-first enterprise Agent platform that borrows Hermes ideas:
- Agent loop
- skill loading
- tool calling
- tool registry
- model routing
- events
- sessions/messages
- audit/cost concepts

But it should avoid Hermes-specific MVP complexity:
- CLI-first structure
- broad gateway/messaging adapter system
- local terminal/filesystem assumptions
- full profile/plugin complexity

## Recommended platform sentence

> Web users create an Agent Run through API Gateway; Agent Runtime chooses a Feature/Skill/Tool path through Registry, checks Policy and Sensitive rules, calls an LLM and governed tools, streams RunEvents back to the UI, and writes Audit/Cost/Sensitive/Citation records for governance.

## Service split used in the discussion

1. `web-app`
   - Feature menu, chat UI, upload UI, citations, sensitive warnings, admin entry.
2. `api-gateway`
   - Public API, auth, session/message API, SSE, upload proxy, admin query API.
3. `agent-runtime`
   - Hermes-like core: Skill Router, Prompt Builder, Model Router, Tool Executor, tool-call loop, RunEvent producer.
4. `registry-service`
   - FeatureContract, SkillContract, ToolContract, capability bundles, versions, allowed tools.
5. `policy-service`
   - UserContext, feature/skill/tool/knowledge permission decisions, future SSO.
6. `knowledge-service`
   - Cloud Storage to Markdown integration, file metadata, chunks, embeddings, retrieval, citations.
7. `governance-service`
   - Audit events, sensitive hits, usage/cost, errors, admin reports.

## Contracts to define before implementation

Freeze these before scaffolding code:

- `UserContext`
- `FeatureContract`
- `SkillContract`
- `ToolContract`
- `RunRequest`
- `RunResponse`
- `RunEvent`
- `MessageContract`
- `ToolCallContract`
- `PolicyDecision`
- `SensitiveHitContract`
- `RetrievalRequest`
- `RetrievalResult`
- `CitationContract`
- `AuditEventContract`
- `CostContract`
- `FileDocumentContract`

## Canonical runtime sequence

1. User logs in.
2. API Gateway resolves UserContext.
3. Frontend loads visible Feature list.
4. User selects a feature or free-types.
5. API Gateway creates session/message/run.
6. Agent Runtime receives RunRequest.
7. Runtime resolves feature/skill/tool set from Registry.
8. Runtime asks Policy Service for feature/skill permission.
9. Runtime calls Sensitive detection on input.
10. Prompt Builder assembles system prompt, skill prompt, user context, allowed tools, and optional RAG context.
11. Model Router calls LLM.
12. If LLM asks for tool call, Runtime checks tool permission, executes tool, records tool result and citations, then returns tool result to LLM.
13. Runtime obtains final answer.
14. Sensitive detection checks output.
15. Governance records messages, events, tools, citations, sensitive hits, cost, and errors.
16. API Gateway streams RunEvents to frontend.
17. Frontend renders answer, citations, tool status, warnings, and errors.

## Event vocabulary suggested in session

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

## Initial repo shape

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

## Discussion pitfall to avoid

Do not answer with a generic chatbot architecture. The user explicitly wants:
- Hermes-like custom platform
- contract-first service boundaries
- Cloud Run deployment
- local Docker simulation
- microservice-style splits
- governance as a first-class requirement

When continuing this work, propose concrete contracts and flows before code.