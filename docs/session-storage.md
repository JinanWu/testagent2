# Session storage 基礎功能說明

本文記錄本專案目前的 Hermes-like session 儲存基礎能力。這些功能是 AgentRuntime、CLI、工具與未來 Gateway 共用的底層能力；目標不是完整複製 Hermes 內部所有 session 系統，而是先建立可保存、可搜尋、可回放、可壓縮分裂、可 rewind 的 SQLite session store。

## 設計目標

1. 不再把對話視為可覆寫的 transcript snapshot。
2. 以 append-first 的方式保存 message rows，避免壓縮、重試或 crash recovery 時丟失原始對話。
3. 壓縮時使用 session split：舊 session 保留原文，新 session 保存壓縮後工作上下文，並以 lineage 連回舊 session。
4. 保存足夠 metadata，讓未來可以回放 tool call、tool result、provider finish reason、token usage 與平台訊息 id。
5. 提供 `session_search` 工具，讓模型可搜尋過去 session。
6. 提供 soft-delete rewind，讓 `/retry`、`/undo` 類功能未來可建立在 audit-safe 的 rewrite 流程上。

## 主要檔案

- `繁中代理/工作階段庫.py`
  - SQLite schema、migration、message append、FTS、session lineage、rewind、compression lock。
- `繁中代理/代理執行階段.py`
  - runtime 持久化 user/assistant/tool messages，寫入 provider usage，壓縮後切換 active session。
- `繁中代理/工具.py`
  - `session_search` tool handler 與本機工具登錄。
- `繁中代理/工作階段上下文.py`
  - active session id 與 session DB path 的 ContextVar/env 傳遞。
- `繁中代理/cli.py`
  - CLI gateway，支援一般對話、session search、rewind。
- `tests/test_session_sqlite.py`
  - session store 的主要 regression tests。

## SQLite schema 概念

目前核心資料表：

- `sessions`
  - 保存 session metadata、parent/child lineage、token counters、compression/rewind counters、system prompt、cwd、model、billing/handoff 預留欄位。
- `messages`
  - 保存 canonical message rows，包含 `role`、`content`、`content_json`、tool metadata、reasoning metadata、finish reason、platform message id、`active`。
- `messages_fts`
  - 一般 FTS5 搜尋索引。
- `messages_fts_trigram`
  - CJK/trigram 搜尋索引；不支援時會 fallback。
  - FTS 結構建立與全量重建已拆開；日常開 DB 只確保 table/trigger 存在，不會每次搜尋都清空重建索引。全量重建只在 migration/repair marker 需要時執行。
- `compression_locks`
  - 壓縮互斥鎖，避免多個 runtime 同時壓縮同一 session。
- `schema_version`
  - 目前 schema 版本。
- `state_meta`
  - session store 狀態 metadata。

## Append-first messages

`寫入訊息清單(session_id, 訊息清單)` 只 append DB 尚未保存的尾端 messages，不會先刪除再重建。

另有兩個基礎 API：

- `附加單一訊息(...)`
  - append 單一 message。
- `替換訊息清單(...)`
  - 將既有 active messages 標記為 `active=0`，再 append 新 canonical transcript。

為了對齊 Hermes 命名，也提供英文相容別名：

- `append_message`
- `replace_messages`

注意：`replace_messages` 不是物理刪除；舊 rows 仍可用 `包含停用=True` 讀回，用於 audit/debug。

## Message metadata

`messages` 會保存並回放下列 metadata：

- `tool_call_id`
- `tool_calls`
- `tool_name`
- `token_count`
- `finish_reason`
- `reasoning`
- `reasoning_content`
- `reasoning_details`
- `codex_reasoning_items`
- `codex_message_items`
- `platform_message_id`
- `observed`
- `active`

Runtime 現在會把 assistant message 的 `finish_reason` 寫入 DB。真實 provider 是否提供 reasoning 欄位，取決於 provider adapter；儲存層已可保存與回放。

## Session metadata 與 usage counters

`sessions` schema 已包含：

- `source`
- `user_id`
- `model`
- `model_config`
- `system_prompt`
- `parent_session_id`
- `compressed_from_session_id`
- `prompt_tokens`
- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `message_count`
- `tool_call_count`
- `api_call_count`
- `compression_count`
- `cwd`
- `billing_*`
- `handoff_*`
- `rewind_count`
- `archived`

Runtime 每次 provider response 後會呼叫 `更新模型使用量(...)`，累計：

- `api_call_count`
- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`

支援 OpenAI-style 與 Gemini-style usage keys，例如：

- `prompt_tokens`
- `completion_tokens`
- `prompt_token_count`
- `candidates_token_count`
- `cached_content_token_count`
- `thoughts_token_count`

目前已實作基礎營運層：CLI/runtime 可寫入 `user_id`、`source`、`model_config`；每次 provider usage 更新後會依本地 pricing table 寫入 `estimated_cost_usd`、`cost_status='estimated'`、`cost_source='local_pricing_table'` 與 `pricing_version`。尚未實作 actual billing 對帳、handoff 狀態更新與 Gateway 自動 user context 注入。

## Compression session split

壓縮不是在同一個 session 內覆寫歷史，而是建立 child session：

1. parent session 保留壓縮前完整原文。
2. parent 設定 `end_reason='compression'`。
3. child session 寫入壓縮後 messages。
4. child 設定 `parent_session_id` 與 `compressed_from_session_id`。
5. active session id 切到 child。

相關 API：

- `建立壓縮後工作階段(...)`
- `取得壓縮Tip(...)`
- `解析Resume工作階段(...)`
- `取得工作階段譜系(...)`
- `讀取訊息(..., include_ancestors=True)`
- `列出工作階段(...)`

`解析Resume工作階段` 會把 root 或中間 child 導向最新 tip，所以使用者 resume 舊 session id 時，runtime 仍會接到最新 active conversation。

## Compression lock

壓縮前會取得 DB-backed lock：

- holder 格式包含 `pid`、`tid`、`agent`、`nonce`。
- lock 過期後可被清除。
- 取得失敗時 runtime 會跳過本次壓縮，保留原訊息繼續執行。
- lock subsystem 因版本差異或 schema 問題失敗時，採 fail-open，避免壓縮永久卡住。

相關 API：

- `取得壓縮鎖(...)`
- `釋放壓縮鎖(...)`
- `壓縮鎖(...)`
- `讀取壓縮鎖Holder(...)`

## session_search 工具

`session_search` 已接到實作，不再只是未啟用 schema。

支援四種呼叫形狀：

### 1. Discovery

搜尋關鍵字，回傳命中 session、snippet、bookends、命中周邊 messages。

```json
{"query": "台北", "limit": 3, "window": 5}
```

### 2. Read

讀取指定 session。

```json
{"session_id": "session-abc"}
```

若訊息過多，會回傳前 20 則與後 10 則，並標記 `truncated=true`。

### 3. Scroll

以 message row id 為中心讀取前後視窗。

```json
{"session_id": "session-abc", "around_message_id": 123, "window": 5}
```

### 4. Browse

不傳 `query` 與 `session_id` 時，瀏覽近期 logical sessions。

```json
{"limit": 10}
```

## session_search DB path 綁定

工具會依下列順序決定 DB path：

1. tool argument `db_path`
2. ContextVar `目前工作階段資料庫路徑`
3. env `TESTAGENT2_SESSION_DB`
4. `~/.testagent2/sessions.sqlite3`

`代理執行階段` 初始化時會把目前 `工作階段庫` 的 DB path 寫入 ContextVar 與 env。因此模型在同一個 runtime 內呼叫 `session_search` 時，不必手動提供 `db_path`，也會搜尋目前 session store。

CLI 自訂 DB 時仍可明確傳入：

```bash
python3 -m 繁中代理.cli --db /tmp/sessions.sqlite3 --session-search 台北
```

## Archive / filters

Session store 提供封存與篩選 API：

- `封存工作階段(session_id)`
- `取消封存工作階段(session_id)`
- `列出工作階段(..., include_archived=False, source=None, user_id=None)`
- `搜尋工作階段(..., include_archived=False, source=None, user_id=None)`

預設列表與搜尋會排除 `archived=1` 的 session。需要包含封存資料時，明確傳入 `include_archived=True`。

CLI 提供：

```bash
python3 -m 繁中代理.cli --db ~/.testagent2/sessions.sqlite3 --archive-session demo
python3 -m 繁中代理.cli --db ~/.testagent2/sessions.sqlite3 --unarchive-session demo
python3 -m 繁中代理.cli --db ~/.testagent2/sessions.sqlite3 --include-archived --session-search README
```

`session_search` 工具也支援：

```json
{"query": "README", "source": "cli", "user_id": "alice", "include_archived": true}
```

## Estimated cost MVP

目前成本計算是本地估算，不做 provider invoice 對帳。

流程：

1. provider 回傳 usage。
2. runtime 呼叫 `更新模型使用量(..., billing_provider=供應商名稱)`。
3. session store 依 `(provider, model)` 從本地 pricing table 取得每百萬 input/output token 單價。
4. 累加 `estimated_cost_usd`。
5. 寫入：
   - `billing_provider`
   - `billing_mode='estimated'`
   - `cost_status='estimated'`
   - `cost_source='local_pricing_table'`
   - `pricing_version='local-pricing-v1'`

目前內建 pricing：

- `fake/fake`: input 0、output 0
- `gemini-adc/gemini-2.5-flash-lite`: input 0.10 / 1M tokens、output 0.40 / 1M tokens

未知 provider/model 會 fallback 為 0 成本，但仍維護 token counters 與 pricing version。

## Rewind / soft-delete

`rewind到訊息(session_id, message_id)` 會把指定 message row id 及之後的 active rows 設為 `active=0`。

特性：

- 不物理刪除 rows。
- `讀取訊息(session_id)` 預設只回傳 active messages。
- `讀取訊息(session_id, 包含停用=True)` 可讀回 audit 全量。
- session 的 `rewind_count` 會遞增。

Runtime 提供：

- `代理執行階段.rewind到訊息(...)`

CLI 提供：

```bash
python3 -m 繁中代理.cli --mode fake --db ~/.testagent2/sessions.sqlite3 --session demo --rewind-to-message-id 123
```

目前尚未實作 Hermes slash command 型的 `/retry`、`/undo`，但底層 rewind 與 `replace_messages` 已可供後續接上。

## CLI 使用範例

一般對話：

```bash
AIAGENT_MODEL_MODE=fake python3 -m 繁中代理.cli --session demo "請讀取 README"
python3 -m 繁中代理.cli --session demo --user-id alice --source cli --model-config-json '{"temperature":0.2}' "記錄 metadata"
```

搜尋 session：

```bash
python3 -m 繁中代理.cli --db ~/.testagent2/sessions.sqlite3 --session-search README
```

Rewind：

```bash
python3 -m 繁中代理.cli --mode fake --db ~/.testagent2/sessions.sqlite3 --session demo --rewind-to-message-id 123
```

## 測試

主要測試在：

- `tests/test_session_sqlite.py`

目前覆蓋：

- append-first 不刪除既有 rows
- compression session split 與 lock
- WAL / cross-connection compression lock
- FTS 搜尋中文與 tool metadata
- 舊 schema migration
- A→B→C compression chain projection
- rewind soft-delete audit
- ContextVar / HERMES_SESSION_ID / hooks
- metadata roundtrip
- `append_message` / `replace_messages`
- `session_search` discovery / read / scroll / browse
- runtime usage counters
- CLI search / rewind
- `user_id` / `source` / `model_config` runtime 與 CLI 寫入
- archive/unarchive API 與 CLI
- list/search filters
- estimated cost MVP

建議驗證指令：

```bash
python3 -m py_compile 繁中代理/工作階段庫.py 繁中代理/代理執行階段.py 繁中代理/工具.py 繁中代理/cli.py 繁中代理/工作階段上下文.py tests/test_session_sqlite.py
python3 -m pytest tests/test_session_sqlite.py -q
python3 -m pytest -q
python3 scripts/檢查繁中文檔.py
git diff --check
```

## 與 Hermes 仍有差距

目前已具備 Hermes-like session store 的基礎，但仍不是完整 Hermes session 系統。

尚未完成：

1. actual billing 對帳與 provider invoice integration。
2. handoff 狀態更新。
3. Gateway SessionEntry 與多平台 user_id/source 注入。
4. 逐步 migration chain，例如 v1→v2→v3 的明確版本升級步驟。
5. malformed DB / malformed FTS repair。
6. NFS/SMB WAL 專用偵測與 fallback。
7. `/retry`、`/undo` slash command 與 runtime 自動 `replace_messages` 流程。

## 維護注意事項

1. 不要把壓縮改回同 session 覆寫；壓縮應保持 session split。
2. 不要物理刪除 message rows 來實作 undo/retry；請用 `active=0` 或 `replace_messages`。append 游標必須以 `active=1` transcript 為準，避免 inactive audit rows 讓新訊息漏寫。
3. 新增 message metadata 欄位時，要同步更新：
   - schema
   - `_附加訊息清單`
   - `_資料列轉訊息`
   - migration/backfill
   - tests
4. 新增 session metadata 欄位時，要同步更新：
   - schema
   - `補齊欄位`
   - 建立/壓縮複製流程
   - runtime 寫入點
5. `session_search` 若新增呼叫形狀，需同時補工具 handler 與 tests。
