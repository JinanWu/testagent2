# aiagent Hermes-like memory parity notes

Use this reference when improving `/Users/wujinan/Documents/aiagent` memory behavior to match Hermes Agent.

## Workflow lessons

- Work on the branch the user is actively testing unless they explicitly ask for a new base. In the memory-parity session the active branch was `refactor/backend-module-split`; switching to `main` first was wrong because it lost alignment with the modularized code the user was testing.
- If a requested scope is memory parity, do not detour into unrelated platform features such as login/auth, even if the app currently uses header-based user simulation. Ask or stay on the stated memory feature.
- In aiagent, default tests may instantiate real Gemini unless `AIAGENT_MODEL_MODE=fake` is set. Use fake mode for deterministic platform regression tests, then run real Gemini/API tests separately only when credentials are confirmed.

## Hermes memory parity surfaces to implement/check

1. Curated memory store
   - Separate `user` profile facts from `memory`/project notes.
   - Enforce compact character budgets similar to Hermes (`USER PROFILE` around 1375 chars, `MEMORY` around 2200 chars).
   - Deduplicate exact entries.
   - Support `add`, `replace`, `remove`, and `list`.
   - Allow `replace/remove` by unique `old_text`; if multiple matches exist, return an ambiguity error instead of guessing.

2. Prompt injection safety
   - Clean invisible Unicode/control-ish prompt-risk characters before injection.
   - Scan memory entries for prompt-injection or exfiltration language.
   - Do not inject dangerous memory text verbatim; render a `[BLOCKED: ...]` marker so the entry can still be discovered and removed.

3. Prompt rendering
   - Render Hermes-style sections, e.g. `USER PROFILE (who the user is) [x% — used/limit chars]` and `MEMORY (agent/project notes) [...]`.
   - Use `§` separators for curated facts.
   - Keep latest user message authoritative if memory conflicts.

4. Hermes-style memory capture loop
   - Do not make hard-coded deterministic regex auto-save the primary memory architecture. Hermes does not primarily parse `我的職業是 X` with rules; it relies on a strong `memory` tool schema, foreground model tool use, and a post-turn background review agent.
   - Strengthen the `memory` tool description so the model knows when to save: user corrections, “remember this” / “don’t do that again”, preferences, personal details such as name/role/timezone/coding style, environment facts, stable project conventions, API quirks, and recurring workflow lessons.
   - Strengthen the system prompt so the model must actually call `memory` before saying “我記下了/已保存/以後我會”. A generic consistency guard is preferred over content-specific regex: if the assistant claims it remembered something but no memory write occurred, trigger a background review or rewrite the answer.
   - Implement a Hermes-like background review: after the answer is delivered, review the recent conversation and use the `memory` tool if durable user profile facts or preferences emerged. This should be LLM-driven, tool-limited, and best-effort.
   - Deterministic extraction may exist only as an optional fallback/test harness for high-confidence demos, not as the main design. If enabled, keep it behind a config flag and avoid growing a CRM-like list of one-off regex rules.
   - Example regression still matters: session A user says `我的職業是資料科學家`; session B asking `我的職業是什麼？` should answer `資料科學家`. Prefer passing this via foreground tool call or background review rather than a profession-specific regex.

5. session_search parity
   - Implement Hermes calling shapes:
     - `query` only: discovery with `snippet`, `match_message_id`, ±window `messages`, `bookend_start`, `bookend_end`, `messages_before`, `messages_after`.
     - `session_id` only: read full session or a bounded head/tail version.
     - `session_id + around_message_id`: scroll window.
     - empty args: browse recent sessions.
   - Always include CJK `LIKE` fallback; SQLite FTS5 tokenization may miss Chinese terms.
   - For personal-memory questions (`職業`, `名字`, `體重`, `身高`, `偏好`), query the term; do not browse with empty args.
   - Hard-cap tool output. `session_id` read shape and broad discovery can explode model context if old sessions contain huge prompts/tool results. For large sessions, return bounded head/tail plus truncation metadata, shorten long `content`/`snippet` fields, clamp `limit`/`window`, and if the JSON still exceeds the cap return a valid JSON preview/summary rather than slicing the JSON string into invalid output.

6. Background review / provider layer
   - Add a post-turn review hook that can save memory when the assistant says it recorded something but no foreground memory write occurred.
   - Add an external memory provider interface even if the initial implementation is no-op: `initialize`, `system_prompt_block`, `prefetch`, `sync_turn`, `queue_prefetch`, `on_memory_write`, `shutdown`.
   - Fence recalled provider context as reference, not as new user input.

7. Tool-call user attribution
   - Do not let model-initiated `memory` tool calls default to global `*` when the model omits `user_id`.
   - Carry the current runtime user into tool execution with a context variable (e.g. `Hermes工具UserId`) and have local memory tools use it as the default `user_id`.
   - Treat `user_id='*'` cautiously: global `memory`/`project` notes may be shared, but global `target='user'` profile rows must NOT be injected into every user's USER PROFILE or returned as visible user memories. Add a regression for this, because stale test data can otherwise make every user appear to have the same profession/profile facts.
   - Add a regression where the model calls `memory(action=add, target=user, content=...)` without `user_id`; the row must belong to the current user, not global `*`.

8. Profile fact consolidation
   - Deterministic auto-save and model memory calls can both record the same fact in different wording. Treat stable profile facts as categorized facts and update the existing category instead of adding duplicates.
   - Useful categories: `職業`, `姓名`, `身高`, `體重`, `時區`, `血型`.
   - Regression: adding `使用者的職業是 資料科學家。` followed by `[職業]：資料科學家` should leave one job memory with the latest content.

## Verification checklist

- `python3 -m py_compile` on changed backend modules.
- `AIAGENT_MODEL_MODE=fake python3 -m pytest -q` for deterministic regression.
- Add focused tests for:
  - foreground model `memory` tool calls and cross-session prefetch.
  - the generic “claimed to remember but no memory write occurred” LLM review path.
  - absence of one-off deterministic regex auto-save when no memory tool/review writes.
  - dangerous memory blocked from prompt.
  - `session_search` discovery/read/scroll/browse shapes.
  - `session_search` output caps for huge sessions; output must remain valid JSON even when truncated.
  - CJK fallback for Chinese queries.
  - `old_text` replace/remove ambiguity handling.
  - current-user attribution for model memory calls without `user_id`.
  - global `target=user` rows are not visible to arbitrary users, while global memory/project notes can still be shared.
- If running full tests without fake mode, capture the real Gemini error before assuming code failure; Vertex ADC/permissions can be environment-dependent.
- When the user is off the company network/account and asks to use a personal GCP project, run real-model verification with `AIAGENT_GCP_PROJECT=<their project>` rather than changing source defaults. In one successful verification, `AIAGENT_GCP_PROJECT=trade-397602 python3 -m pytest -q` passed all tests.
- After full tests pass, run an API-level reproduction of the original memory bug: create session A with `我的職業是資料科學家`, inspect DB memories for the current user, then create session B asking `我的職業是什麼？`. Verify the answer contains `資料科學家` and no new global `*` memory is created by model-side memory calls.
