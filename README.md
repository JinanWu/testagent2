# testagent2

Hermes-style CLI Agent MVP，重點是保留 OpenAI-compatible canonical message shape、Hermes prompt 分層順序、SQLite session persistence、context compression、tool-call loop，以及以 gcloud ADC/Vertex AI Gemini 作為 provider adapter。

## 執行

```bash
python3 -m 繁中代理.cli --help
AIAGENT_MODEL_MODE=fake python3 -m 繁中代理.cli --session demo "請讀取 README"
```

Gemini smoke test 需要已完成 `gcloud auth application-default login`，並設定：

```bash
export AIAGENT_MODEL_MODE=gemini
export AIAGENT_GCP_PROJECT=trade-397602
export AIAGENT_GCP_LOCATION=global
export AIAGENT_MODEL=gemini-flash-lite  # 會正規化為 Vertex AI 的 gemini-2.5-flash-lite
python3 -m 繁中代理.cli --session gemini-smoke "用一句繁體中文回答：你可以運作嗎？"
```

## Hermes 對照摘要

本專案先讀取 `/Users/wujinan/Documents/hermes-agent/agent/system_prompt.py`、`prompt_builder.py`、`turn_context.py`、`conversation_loop.py` 後實作以下等價 MVP：

1. system prompt 分為 stable/context/volatile，並依 Hermes 順序組裝：identity/help guidance/task completion/tool guidance/steer/tool enforcement/model guidance/skills/environment/platform/context files/memory/time-model-provider。
2. 內部訊息一律使用 OpenAI-compatible dict：`role`、`content`、`tool_calls`、`tool_call_id`。
3. provider adapter 才把 canonical messages 轉成 Gemini Vertex AI 呼叫格式。
4. tool schema 透過 request/tool registry 傳入 provider，不塞成一般 system-prompt prose；`assets/hermes_core_tool_schemas.json` 從 Hermes `_HERMES_CORE_TOOLS` 擷取 48 個 core tool schema。
5. 使用者 turn 進入後立即寫入 SQLite 以提高 crash resilience；assistant tool_call 與 tool result 先加到 working messages，於持久化點 flush。
6. context compression 在 preflight 與 tool loop 後檢查；超過 context window 約 50% 且高於 minimum floor 時，保留開頭與近期尾端，摘要中間歷史。
7. `gemini-flash-lite` 會正規化為 Vertex AI 可用的 `gemini-2.5-flash-lite`，以支援低成本 smoke test。

## Session storage

Session 儲存層已整理成獨立說明：

- `docs/session-storage.md`

內容涵蓋 append-first messages、compression session split、FTS / `session_search`、metadata、usage counters、rewind soft-delete、CLI 用法、測試與目前仍未達 Hermes parity 的差距。

## 測試

```bash
AIAGENT_MODEL_MODE=fake python3 -m pytest -q
python3 scripts/檢查繁中文檔.py
```
