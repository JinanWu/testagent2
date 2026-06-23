# Gateway → Runtime context handoff

Use this note when designing a Hermes-like web/API gateway in front of an Agent Runtime.

## Core lesson

A gateway should not send only `session_id`, `user_id`, and raw message text. It should send or make available a standardized execution envelope that lets the Runtime reconstruct the current turn safely:

- current user message, including normalized attachment references;
- conversation history or a clear instruction that Runtime should load history from storage;
- user/session/source context, such as platform, chat/thread, role, department, data scopes;
- selected feature / agent / skill hint from the UI, without forcing the Runtime's final routing decision;
- execution options, such as streaming, timeout, max tool steps, dry-run, cancellation key;
- governance requirements, such as audit, sensitive-data check, cost tracking;
- trace/request ids and response-channel metadata for events.

## Hermes-informed pattern

Hermes gateway currently runs mostly in-process with `AIAgent`, not as a separate Runtime microservice. Still, its conceptual handoff has three useful context layers:

1. `conversation_history`: gateway loads the transcript for the session, optionally runs pre-agent hygiene compression when it is too large, then passes the cleaned agent history into `run_conversation`.
2. `SessionContext` / ephemeral prompt: gateway builds a source prompt describing platform, chat, thread, user, home channels, and multi-user session shape. This tells the agent where the request came from.
3. task-local session context: gateway sets task-local context variables for platform/chat/thread/user/session/message so tools, approvals, background notifications, and delivery helpers route back to the correct place without relying on global process environment.

## Design recommendation for a service split

For an enterprise web platform, prefer an explicit `RunRequest` contract even if Hermes itself does not always serialize one over HTTP:

```ts
interface RunRequest {
  request_id: string;
  run_id: string;
  session_id: string;
  message_id: string;
  user: UserContext;
  input: {
    text: string;
    attachments: AttachmentRef[];
    locale?: string;
    client_timezone?: string;
  };
  intent_hint?: {
    selected_feature_id?: string;
    selected_agent_id?: string;
    selected_skill_id?: string;
    free_input: boolean;
  };
  conversation_context: {
    recent_messages?: MessageContract[];
    conversation_summary?: string;
    runtime_loads_from_storage?: boolean;
  };
  execution_options: {
    stream: boolean;
    max_steps: number;
    timeout_ms: number;
    dry_run?: boolean;
  };
  governance: {
    audit_required: boolean;
    sensitive_check_required: boolean;
    cost_tracking_required: boolean;
  };
  response_channel: {
    mode: "sse" | "websocket" | "polling";
    event_stream_id: string;
  };
  trace: {
    trace_id: string;
    source: "web" | "api" | "job";
  };
}
```

## Boundary rule

Gateway owns request normalization and delivery:

- authenticate/authorize the source at the edge;
- create or resolve session/message/run ids;
- normalize attachments to references, not raw blobs;
- load or point to conversation history;
- attach response-channel and trace metadata;
- forward Runtime events back to the frontend.

Runtime owns reasoning and execution:

- skill routing;
- registry/policy checks;
- prompt building;
- model/tool/RAG loop;
- answer/citation/warning generation;
- RunEvent emission.

## Pitfalls

- Do not make Gateway assemble the final LLM prompt. That couples frontend/API concerns to agent reasoning and makes skill evolution hard.
- Do not omit conversation history. The Runtime either needs recent messages in the request or must have a reliable way to load them by `session_id`.
- Do not pass raw uploaded files through the RunRequest. Pass file/document ids and storage URIs.
- Preserve prompt-cache friendliness: stable session/system context should not churn every turn unless platform/user/permission/tool configuration really changed.
