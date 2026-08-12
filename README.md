# testagent2

Hermes-style CLI Agent MVP，重點是保留 OpenAI-compatible canonical message shape、Hermes prompt 分層順序、SQLite session persistence、context compression、tool-call loop，以及以 gcloud ADC/Vertex AI Gemini 作為 provider adapter。

## 執行

```bash
python3 -m 繁中代理.cli --help
python3 -m 繁中代理.cli users --help
python3 -m 繁中代理.cli auth --help
AIAGENT_MODEL_MODE=fake python3 -m 繁中代理.cli --session demo "請讀取 README"
```

## 使用者、登入與隔離

本專案已支援本機使用者與 UserContext。正式使用建議先建立使用者並登入；未登入時會使用本機 `local` admin fallback 方便開發，`--user-id` / `TESTAGENT2_USER_ID` 則保留給 dev/test fallback。

```bash
# 建立使用者；--workdirs 會限制檔案與 terminal 工具可操作的目錄
python3 -m 繁中代理.cli users create alice \
  --password '<密碼>' \
  --workdirs /path/to/repo

# 查看使用者
python3 -m 繁中代理.cli users list

# 設定可用工具 / 技能；逗號分隔，* 表示全部允許
python3 -m 繁中代理.cli users set-tools alice read_file,search_files,terminal,skills_list,skill_view,session_search,memory
python3 -m 繁中代理.cli users set-skills alice hermes-agent,verification-and-debugging
python3 -m 繁中代理.cli users set-workdirs alice /path/to/repo
python3 -m 繁中代理.cli users set-skill-roots alice '*'

# 登入、確認目前登入者、登出
python3 -m 繁中代理.cli auth login alice
python3 -m 繁中代理.cli auth whoami
python3 -m 繁中代理.cli auth logout

# 登入後執行 agent；runtime 會用目前登入者的 UserContext
python3 -m 繁中代理.cli --session demo "請列出目前可用技能"
```

登入後，agent 會依目前使用者限制：

- session owner/source：不能 resume、讀取、rename、archive、rewind 其他使用者的 session。
- tools：只 expose `enabled_tools`，硬呼叫未授權工具也會被拒。
- workdir：read/write/patch/search/terminal 只能操作 `allowed_workdirs` 內的路徑。
- skills：prompt、`skills_list`、`skill_view` 只看得到 `enabled_skills` 與 `skill_roots` 允許的技能。
- memory：system prompt 與 memory tool 使用該使用者自己的 `memory_home`。

`skill_roots` 有三種語意：

- `*`：使用專案內建 `assets/hermes_skills`。
- 空清單：不注入任何 skill 摘要，`skills_list` 也不回傳內建技能。
- 路徑清單：只掃描指定技能根目錄；搭配 `enabled_skills` 再做技能名稱過濾。

常用環境變數：

```bash
export TESTAGENT2_AUTH_FILE=/tmp/testagent2-auth.json  # 指定本機 token 檔案，測試時常用
export TESTAGENT2_REQUIRE_LOGIN=1                     # 沒登入就拒絕執行 agent
export TESTAGENT2_USER_ID=alice                       # dev/test fallback，不建議正式使用
export TESTAGENT2_PASSWORD='<密碼>'                    # 非互動測試時供 users create / auth login 讀取
```

本機 auth token 預設 24 小時後過期，並記錄登入時使用的 SQLite DB 路徑；切換 `--db` 時，CLI 只會採用對應 DB 的 token，避免不同資料庫的登入狀態互相污染。

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

## 儲存後端切換（SQLite / BigQuery）

儲存後端由環境變數 `STORAGE_BACKEND` 決定，可寫在專案根目錄 `.env`。上層程式碼一律透過 `繁中代理/儲存.py` 的工廠（`建立工作階段庫` / `建立使用者庫`）取得儲存物件，切換後端不需改動呼叫端。

```bash
STORAGE_BACKEND=sqlite     # 地端開發（預設）：全部存本機 SQLite
STORAGE_BACKEND=bigquery   # 雲端：核心資料改存 BigQuery
```

各表歸屬：

| 表 | `sqlite` 模式 | `bigquery` 模式 |
| --- | --- | --- |
| `sessions` / `messages` / `session_usage_events` | 本機 SQLite | **BigQuery** |
| `users` / `user_settings` / `auth_sessions` | 本機 SQLite | **BigQuery** |
| `compression_locks`、FTS、`state_meta`、`schema_version` | 本機 SQLite | **仍本機 SQLite** |

- 用量 token 在 BigQuery 模式改為獨立的 append-only 表 `session_usage_events`（每次模型呼叫 INSERT 一列、不累加；總量由查詢時 `SUM` 得出）。
- 壓縮鎖不進 BigQuery（無交易/原子性），委派本機 SQLite。


BigQuery 相關環境變數（沿用 `管理部_bigquery` 的 `.env` 載入）：

```bash
export CORE_BQ_PROJECT=lab-cola-rd    # 必填：BigQuery 專案
export CORE_BQ_DATASET=agent_core     # dataset，預設 agent_core
# export CORE_BQ_LOCATION=US           # 可選 job location
export CORE_BQ_SKIP_DDL=1             # 表已建好時跳過啟動建表，加速；新建/改 schema 時改回 0
```

使用前需完成 `gcloud auth application-default login`，並確保帳號對 `CORE_BQ_PROJECT` 有 BigQuery 權限。



## 測試

```bash
AIAGENT_MODEL_MODE=fake python3 -m pytest -q
python3 scripts/檢查繁中文檔.py
```

繁中checker採owner核准的嚴格baseline ratchet：現有1,463項是公開的技術債，不代表符合規範；只有finding集合與`scripts/繁中checker-baseline.json`完全相同，或問題真正歸零時才通過。新增、移除、替換、移動任何finding都會失敗並顯示差異。Baseline不得在feature或acceptance卡為恢復綠燈而刷新；只有獨立修債變更、完整checker測試及獨立review通過後，才可更新manifest。
