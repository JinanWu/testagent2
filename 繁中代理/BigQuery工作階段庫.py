"""BigQuery 工作階段庫（雲端儲存後端）。

功能：
    以 BigQuery 作為 sessions / messages 的儲存後端，並依既定設計把「每次模型
    呼叫的用量」拆到獨立的 append-only 表 `session_usage_events`（只 INSERT、不
    累加；總量由查詢時 SUM 得出）。

    與本機 SQLite 版（`工作階段庫`）維持相同的方法介面，讓上層透過 `儲存.py`
    工廠切換後端而不需修改呼叫端。

    寫入一律走 DML（非 streaming insert）以取得「寫後即讀」一致性。訊息量大時
    可再優化為批次/streaming，屬未來事項。

    「鎖」不適合 BigQuery（無交易/主鍵原子性），因此壓縮鎖仍委派給本機 SQLite
    （單實例、暫時性即可）。詳見 [[bigquery-migration-plan]]。

環境變數：
    CORE_BQ_PROJECT: BigQuery 專案 ID。
    CORE_BQ_DATASET: BigQuery dataset，預設 agent_core。
    CORE_BQ_LOCATION: BigQuery job location，可選。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .工作階段庫 import 工作階段庫
from .環境設定 import 應跳過建表, 讀取核心BigQuery設定

會話表 = "sessions"
訊息表 = "messages"
用量事件表 = "session_usage_events"


_已確保會話資料表: set[str] = set()

用量事件型別 = {
    "id": "STRING", "session_id": "STRING", "user_id": "STRING", "created_at": "FLOAT64",
    "model": "STRING", "prompt_tokens": "INT64", "input_tokens": "INT64", "output_tokens": "INT64",
    "cache_read_tokens": "INT64", "cache_write_tokens": "INT64", "reasoning_tokens": "INT64",
    "estimated_cost_usd": "FLOAT64", "billing_provider": "STRING", "pricing_version": "STRING",
}
訊息型別 = {
    "id": "STRING", "session_id": "STRING", "message_index": "INT64", "role": "STRING",
    "content": "STRING", "content_json": "STRING", "tool_call_id": "STRING", "tool_calls": "STRING",
    "tool_name": "STRING", "token_count": "INT64", "finish_reason": "STRING", "reasoning": "STRING",
    "reasoning_content": "STRING", "reasoning_details": "STRING", "codex_reasoning_items": "STRING",
    "codex_message_items": "STRING", "platform_message_id": "STRING", "observed": "BOOL",
    "active": "BOOL", "created_at": "FLOAT64", "timestamp": "FLOAT64",
}


class BigQuery工作階段庫:
    """以 BigQuery 儲存 sessions / messages / 用量事件的工作階段庫。"""

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化 BigQuery 後端並確保資料集與資料表存在。

        參數：
            資料庫路徑: 本機 SQLite 路徑；BigQuery 模式下僅用於壓縮鎖等暫時性資料。

        返回值：None。
        """
        self.資料庫路徑 = Path(資料庫路徑)
        self.設定 = 讀取核心BigQuery設定()
        self._客戶端 = None
        # 鎖與 FTS 等 SQLite 專屬機制仍走本機：內部持有一個 SQLite 工作階段庫，
        # 只用它的壓縮鎖相關方法，其餘資料方法都打 BigQuery。
        self._本機鎖庫 = 工作階段庫(資料庫路徑)
        # process 內快取，吸收 BigQuery 延遲：只快取「session 內不變」的中繼資料，
        # 以及訊息 append 的下一個 index。重啟即清空，不影響正確性。
        self._會話中繼快取: dict[str, dict[str, Any]] = {}
        self._下一索引快取: dict[str, int] = {}
        self.確保資料集與資料表()

    def _更新中繼快取(self, 工作階段識別碼: str, 資料列: dict[str, Any]) -> None:
        """從 session row 更新中繼快取（只存 session 內不變的欄位）。"""
        self._會話中繼快取[工作階段識別碼] = {
            "source": 資料列.get("source"), "user_id": 資料列.get("user_id"),
            "model": 資料列.get("model"), "model_config": 資料列.get("model_config"),
            "cwd": 資料列.get("cwd"), "billing_provider": 資料列.get("billing_provider"),
        }

    def _套用會話更新(self, 識別碼: str, source, user_id, model, 模型設定JSON, cwd) -> None:
        """對既有 session 套用 COALESCE 更新。"""
        from google.cloud import bigquery

        self._執行DML(
            f"""
            UPDATE `{self._表名(會話表)}`
            SET source=COALESCE(@source, source), user_id=COALESCE(@user_id, user_id),
                model=COALESCE(@model, model), model_config=COALESCE(@model_config, model_config),
                cwd=COALESCE(@cwd, cwd), updated_at=@now
            WHERE id=@id
            """,
            [
                bigquery.ScalarQueryParameter("id", "STRING", 識別碼),
                bigquery.ScalarQueryParameter("source", "STRING", source),
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("model", "STRING", model),
                bigquery.ScalarQueryParameter("model_config", "STRING", 模型設定JSON),
                bigquery.ScalarQueryParameter("cwd", "STRING", cwd),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
            ],
        )

    # ── 連線 ─────────────────────────────────────────────────────────────
    @property
    def 客戶端(self):
        """延遲建立並快取 BigQuery client。"""
        if self._客戶端 is None:
            try:
                from google.cloud import bigquery
            except ImportError as 錯誤:
                raise RuntimeError("缺少 google-cloud-bigquery，無法使用 BigQuery 儲存後端") from 錯誤
            self._客戶端 = bigquery.Client(project=self.設定["project"], location=self.設定["location"])
        return self._客戶端

    def _表名(self, 表: str) -> str:
        """回傳可放進 SQL 的完整表名 `project.dataset.table`。"""
        return f"{self.設定['project']}.{self.設定['dataset']}.{表}"

    # ── DDL：資料集與三張表 ───────────────────────────────────────────────
    def 確保資料集與資料表(self) -> None:
        """建立 dataset 與 sessions / messages / session_usage_events（若不存在）。

        每個 process 對同一 dataset 只實際執行一次 DDL，避免每次建立儲存物件都
        重跑 CREATE TABLE 造成不必要的 BigQuery 往返。
        """
        鍵 = f"{self.設定['project']}.{self.設定['dataset']}"
        if 鍵 in _已確保會話資料表:
            return
        if 應跳過建表():
            _已確保會話資料表.add(鍵)
            return
        from google.cloud import bigquery

        資料集參照 = bigquery.Dataset(f"{self.設定['project']}.{self.設定['dataset']}")
        if self.設定["location"]:
            資料集參照.location = self.設定["location"]
        self.客戶端.create_dataset(資料集參照, exists_ok=True)
        for 語句 in (self._會話表DDL(), self._訊息表DDL(), self._用量事件表DDL()):
            self.客戶端.query(語句).result()
        _已確保會話資料表.add(鍵)

    def _會話表DDL(self) -> str:
        """sessions（精簡後）：只留描述會話的欄位；token/計數已拆到用量事件表。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(會話表)}` (
            id STRING NOT NULL, source STRING, user_id STRING, model STRING,
            model_config STRING, system_prompt STRING, cwd STRING, title STRING,
            parent_session_id STRING, compressed_from_session_id STRING, end_reason STRING,
            prompt_tokens INT64, compression_count INT64, rewind_count INT64,
            billing_provider STRING, billing_base_url STRING, billing_mode STRING,
            handoff_state STRING, handoff_platform STRING, handoff_error STRING,
            archived BOOL, created_at FLOAT64, updated_at FLOAT64, started_at FLOAT64, ended_at FLOAT64
        )
        """

    def _訊息表DDL(self) -> str:
        """messages：與本機版欄位對齊；id 改用 STRING（BigQuery 無自動遞增）。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(訊息表)}` (
            id STRING NOT NULL, session_id STRING NOT NULL, message_index INT64, role STRING,
            content STRING, content_json STRING, tool_call_id STRING, tool_calls STRING,
            tool_name STRING, token_count INT64, finish_reason STRING, reasoning STRING,
            reasoning_content STRING, reasoning_details STRING, codex_reasoning_items STRING,
            codex_message_items STRING, platform_message_id STRING, observed BOOL, active BOOL,
            created_at FLOAT64, timestamp FLOAT64
        )
        """

    def _用量事件表DDL(self) -> str:
        """session_usage_events：每次模型呼叫 INSERT 一列，不累加；對照 session_id + user_id。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(用量事件表)}` (
            id STRING NOT NULL, session_id STRING NOT NULL, user_id STRING, created_at FLOAT64,
            model STRING, prompt_tokens INT64, input_tokens INT64, output_tokens INT64,
            cache_read_tokens INT64, cache_write_tokens INT64, reasoning_tokens INT64,
            estimated_cost_usd FLOAT64, billing_provider STRING, pricing_version STRING
        )
        """

    # ── 壓縮鎖：委派本機 SQLite（BigQuery 不適合做鎖）────────────────────────
    def 壓縮鎖(self, 工作階段識別碼: str, ttl秒: int = 300):
        """壓縮鎖 context manager，委派本機 SQLite。"""
        return self._本機鎖庫.壓縮鎖(工作階段識別碼, ttl秒=ttl秒)

    def 取得壓縮鎖(self, 工作階段識別碼: str, 擁有者: str | None = None, ttl秒: int = 300) -> str | None:
        """取得壓縮鎖，委派本機 SQLite。"""
        return self._本機鎖庫.取得壓縮鎖(工作階段識別碼, 擁有者=擁有者, ttl秒=ttl秒)

    def 釋放壓縮鎖(self, 工作階段識別碼: str, 擁有者: str) -> None:
        """釋放壓縮鎖，委派本機 SQLite。"""
        self._本機鎖庫.釋放壓縮鎖(工作階段識別碼, 擁有者)

    def 讀取壓縮鎖Holder(self, 工作階段識別碼: str) -> str | None:
        """讀取壓縮鎖持有者，委派本機 SQLite。"""
        return self._本機鎖庫.讀取壓縮鎖Holder(工作階段識別碼)

    def 建立壓縮鎖Holder(self, agent標籤: str | None = None) -> str:
        """建立壓縮鎖 holder 字串，委派本機 SQLite。"""
        return self._本機鎖庫.建立壓縮鎖Holder(agent標籤)

    # ── 建立 / 讀取工作階段 ────────────────────────────────────────────────
    def 建立或讀取工作階段(
        self,
        工作階段識別碼: str | None = None,
        parent_session_id: str | None = None,
        source: str = "cli",
        user_id: str | None = None,
        model: str | None = None,
        model_config: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> str:
        """建立新工作階段或確認既有工作階段存在；回傳 session id。"""
        from google.cloud import bigquery

        識別碼 = 工作階段識別碼 or f"session-{uuid.uuid4().hex[:12]}"
        是新建 = 工作階段識別碼 is None
        目前時間 = time.time()
        模型設定JSON = _轉JSON(model_config)
        傳入 = {"source": source, "user_id": user_id, "model": model, "model_config": 模型設定JSON, "cwd": cwd}

        # 快取命中：session 已知存在，若傳入值與快取一致就完全跳過查詢
        快取 = self._會話中繼快取.get(識別碼)
        if 快取 is not None:
            if user_id and 快取.get("user_id") and 快取["user_id"] != user_id:
                raise PermissionError(f"使用者 {user_id} 無權接管 session {識別碼}")
            變更 = {鍵: 值 for 鍵, 值 in 傳入.items() if 值 is not None and 快取.get(鍵) != 值}
            if not 變更:
                return 識別碼
            self._套用會話更新(識別碼, source, user_id, model, 模型設定JSON, cwd)
            快取.update(變更)
            return 識別碼

        # 未快取：新建則免查存在性，直接 INSERT；否則查一次
        現有 = None if 是新建 else self._查詢單列(
            f"SELECT user_id, source, model, model_config, cwd, billing_provider FROM `{self._表名(會話表)}` WHERE id=@id",
            [bigquery.ScalarQueryParameter("id", "STRING", 識別碼)],
        )
        if 現有 and user_id and 現有.get("user_id") and 現有["user_id"] != user_id:
            raise PermissionError(f"使用者 {user_id} 無權接管 session {識別碼}")
        if 現有 is None:
            self._插入單列(會話表, {
                "id": 識別碼, "source": source, "user_id": user_id, "title": 識別碼,
                "parent_session_id": parent_session_id, "model": model,
                "model_config": 模型設定JSON, "cwd": cwd, "archived": False,
                "created_at": 目前時間, "started_at": 目前時間, "updated_at": 目前時間,
            }, 會話表型別)
            self._會話中繼快取[識別碼] = {**傳入, "billing_provider": None}
        else:
            self._套用會話更新(識別碼, source, user_id, model, 模型設定JSON, cwd)
            self._會話中繼快取[識別碼] = {
                "source": source or 現有.get("source"), "user_id": user_id or 現有.get("user_id"),
                "model": model or 現有.get("model"), "model_config": 模型設定JSON or 現有.get("model_config"),
                "cwd": cwd or 現有.get("cwd"), "billing_provider": 現有.get("billing_provider"),
            }
        return 識別碼

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        """讀取單一工作階段資料（回傳最新完整 row），並刷新中繼快取。"""
        from google.cloud import bigquery

        資料列 = self._查詢單列(
            f"SELECT * FROM `{self._表名(會話表)}` WHERE id=@id LIMIT 1",
            [bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼)],
        )
        if 資料列:
            self._更新中繼快取(工作階段識別碼, 資料列)
        return 資料列

    def 檢查工作階段存取(self, 工作階段識別碼: str, user_id: str | None = None, source: str | None = None) -> dict[str, Any] | None:
        """確認可存取指定 session；不符時丟出 PermissionError。

        權限檢查只需 user_id/source，命中中繼快取時直接用快取判斷，不再重查整列。
        """
        工作階段 = self._會話中繼快取.get(工作階段識別碼) or self.讀取工作階段(工作階段識別碼)
        if not 工作階段:
            return None
        if user_id and 工作階段.get("user_id") and 工作階段.get("user_id") != user_id:
            raise PermissionError(f"使用者 {user_id} 無權存取 session {工作階段識別碼}")
        if source and 工作階段.get("source") and 工作階段.get("source") != source:
            raise PermissionError(f"來源 {source} 無權存取 session {工作階段識別碼}")
        return 工作階段

    def 更新系統提示詞(self, 工作階段識別碼: str, 系統提示詞: str) -> None:
        """把穩定的 system prompt 快照寫回 session row。"""
        from google.cloud import bigquery

        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET system_prompt=@sp, updated_at=@now WHERE id=@id",
            [
                bigquery.ScalarQueryParameter("sp", "STRING", 系統提示詞),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
            ],
        )

    def 更新提示Token數(self, 工作階段識別碼: str, token數: int) -> None:
        """保存 provider prompt token usage。"""
        from google.cloud import bigquery

        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET prompt_tokens=@t, updated_at=@now WHERE id=@id",
            [
                bigquery.ScalarQueryParameter("t", "INT64", int(token數)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
            ],
        )

    # ── 訊息讀寫（append-first）────────────────────────────────────────────
    def 讀取訊息(self, 工作階段識別碼: str, 包含停用: bool = False, include_ancestors: bool = False, user_id: str | None = None) -> list[dict[str, Any]]:
        """依序讀取某工作階段的 canonical messages。"""
        from google.cloud import bigquery

        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        識別碼清單 = self.取得工作階段譜系(工作階段識別碼) if include_ancestors else [工作階段識別碼]
        啟用條件 = "" if 包含停用 else " AND active = TRUE"
        # 讀取時以 timestamp 排序（壓縮 child 訊息時間戳晚於 root）
        # 單一 session 時 timestamp 與 message_index 同序，故一律用 timestamp, message_index。
        列清單 = self._查詢多列(
            f"SELECT * FROM `{self._表名(訊息表)}` WHERE session_id IN UNNEST(@sids){啟用條件} ORDER BY timestamp, message_index",
            [bigquery.ArrayQueryParameter("sids", "STRING", 識別碼清單)],
        )
        return [self._資料列轉訊息(列) for 列 in 列清單]

    def 附加單一訊息(self, 工作階段識別碼: str, 訊息: dict[str, Any]) -> int:
        """append 單一 message，回傳其 message_index。"""
        起始索引 = self._下一個訊息索引(工作階段識別碼)
        self._附加訊息清單(工作階段識別碼, [訊息], 起始索引)
        return 起始索引

    def 寫入訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]], 是否使用既有交易: bool = False) -> None:
        """append-first 寫入 working messages，只 append DB 尚未保存的尾端。"""
        from google.cloud import bigquery

        起始索引 = self._下一個訊息索引(工作階段識別碼)
        if 起始索引 >= len(訊息清單):
            self._執行DML(
                f"UPDATE `{self._表名(會話表)}` SET updated_at=@now WHERE id=@id",
                [
                    bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                    bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
                ],
            )
            return
        self._附加訊息清單(工作階段識別碼, 訊息清單[起始索引:], 起始索引)

    def _下一個訊息索引(self, 工作階段識別碼: str) -> int:
        """回傳下一個 message_index。命中快取直接用；否則查一次 MAX 後記住。"""
        from google.cloud import bigquery

        if 工作階段識別碼 in self._下一索引快取:
            return self._下一索引快取[工作階段識別碼]
        列 = self._查詢單列(
            f"SELECT MAX(message_index) AS m FROM `{self._表名(訊息表)}` WHERE session_id=@sid AND active=TRUE",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        目前最大 = (列 or {}).get("m")
        索引 = (int(目前最大) if 目前最大 is not None else -1) + 1
        self._下一索引快取[工作階段識別碼] = 索引
        return 索引

    def _附加訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]], 起始索引: int) -> None:
        """把訊息清單逐列 INSERT 到 messages，並更新 session updated_at。"""
        from google.cloud import bigquery

        目前時間 = time.time()
        待插入: list[dict[str, Any]] = []
        for 偏移, 訊息 in enumerate(訊息清單):
            內容 = 訊息.get("content")
            內容字串 = 內容 if (isinstance(內容, str) or 內容 is None) else json.dumps(內容, ensure_ascii=False)
            工具呼叫清單 = 訊息.get("tool_calls")
            工具呼叫JSON = json.dumps(工具呼叫清單, ensure_ascii=False) if 工具呼叫清單 else None
            工具名 = 訊息.get("name") or 訊息.get("tool_name")
            if not 工具名 and 工具呼叫清單:
                try:
                    工具名 = 工具呼叫清單[0].get("function", {}).get("name")
                except Exception:
                    工具名 = None
            時間戳 = 目前時間 + 偏移 * 0.000001
            待插入.append({
                "id": uuid.uuid4().hex,
                "session_id": 工作階段識別碼,
                "message_index": 起始索引 + 偏移,
                "role": str(訊息.get("role", "")),
                "content": 內容字串,
                "content_json": json.dumps(訊息, ensure_ascii=False),
                "tool_call_id": 訊息.get("tool_call_id"),
                "tool_calls": 工具呼叫JSON,
                "tool_name": 工具名,
                "token_count": 訊息.get("token_count"),
                "finish_reason": 訊息.get("finish_reason"),
                "reasoning": 訊息.get("reasoning"),
                "reasoning_content": 訊息.get("reasoning_content"),
                "reasoning_details": _轉JSON(訊息.get("reasoning_details")),
                "codex_reasoning_items": _轉JSON(訊息.get("codex_reasoning_items")),
                "codex_message_items": _轉JSON(訊息.get("codex_message_items")),
                "platform_message_id": 訊息.get("platform_message_id") or 訊息.get("message_id"),
                "observed": bool(訊息.get("observed")),
                "active": True,
                "created_at": 時間戳,
                "timestamp": 時間戳,
            })
        self._插入多列(訊息表, 待插入, 訊息型別)
        self._下一索引快取[工作階段識別碼] = 起始索引 + len(待插入)
        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET updated_at=@now WHERE id=@id",
            [
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
            ],
        )

    def _資料列轉訊息(self, 列: dict[str, Any]) -> dict[str, Any]:
        """把 messages row（dict）還原成 canonical message dict。"""
        訊息 = json.loads(列.get("content_json") or "{}")
        訊息["role"] = 列.get("role")
        if 列.get("content") is not None:
            訊息["content"] = 列.get("content")
        if 列.get("tool_call_id"):
            訊息["tool_call_id"] = 列.get("tool_call_id")
        if 列.get("tool_name"):
            訊息["name"] = 列.get("tool_name")
            訊息["tool_name"] = 列.get("tool_name")
        if 列.get("tool_calls"):
            try:
                訊息["tool_calls"] = json.loads(列["tool_calls"])
            except (json.JSONDecodeError, TypeError):
                訊息["tool_calls"] = []
        if 列.get("finish_reason"):
            訊息["finish_reason"] = 列.get("finish_reason")
        if 列.get("token_count") is not None:
            訊息["token_count"] = 列.get("token_count")
        if 列.get("reasoning"):
            訊息["reasoning"] = 列.get("reasoning")
        if 列.get("reasoning_content") is not None:
            訊息["reasoning_content"] = 列.get("reasoning_content")
        for 欄位 in ("reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if 列.get(欄位):
                try:
                    訊息[欄位] = json.loads(列[欄位])
                except (json.JSONDecodeError, TypeError):
                    訊息[欄位] = 列[欄位]
        if 列.get("platform_message_id"):
            訊息["message_id"] = 列.get("platform_message_id")
            訊息["platform_message_id"] = 列.get("platform_message_id")
        if 列.get("observed"):
            訊息["observed"] = True
        return 訊息

    # ── 用量：INSERT 一列到 session_usage_events（設計核心）──────────────────
    def 更新模型使用量(self, 工作階段識別碼: str, 使用量: dict[str, Any] | None, api呼叫增量: int = 1, billing_provider: str | None = None) -> None:
        """把單次模型呼叫的用量以 append 事件寫入 session_usage_events（不累加）。

        參數：
            工作階段識別碼: session id。
            使用量: provider 回傳的 usage dict。
            api呼叫增量: 相容參數；append 模型下每列即一次呼叫，不另計。
            billing_provider: 成本 provider 名稱。

        返回值：None。總量請於查詢時對本表 SUM 得出。
        """
        from google.cloud import bigquery

        使用量 = 使用量 or {}
        輸入 = int(使用量.get("input_tokens") or 使用量.get("prompt_tokens") or 使用量.get("prompt_token_count") or 0)
        輸出 = int(使用量.get("output_tokens") or 使用量.get("completion_tokens") or 使用量.get("candidates_token_count") or 0)
        快取讀 = int(使用量.get("cache_read_tokens") or 使用量.get("cached_content_token_count") or 0)
        快取寫 = int(使用量.get("cache_write_tokens") or 0)
        推理 = int(使用量.get("reasoning_tokens") or 使用量.get("thoughts_token_count") or 0)
        會話 = self._會話中繼快取.get(工作階段識別碼)
        if 會話 is None:
            會話 = self._查詢單列(
                f"SELECT user_id, model, billing_provider FROM `{self._表名(會話表)}` WHERE id=@id",
                [bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼)],
            ) or {}
        供應商 = billing_provider or 會話.get("billing_provider") or "unknown"
        模型名稱 = 會話.get("model")
        計價, 成本 = _估算成本(供應商, 模型名稱, 輸入, 輸出)
        self._插入單列(用量事件表, {
            "id": uuid.uuid4().hex, "session_id": 工作階段識別碼, "user_id": 會話.get("user_id"),
            "created_at": time.time(), "model": 模型名稱, "prompt_tokens": 輸入, "input_tokens": 輸入,
            "output_tokens": 輸出, "cache_read_tokens": 快取讀, "cache_write_tokens": 快取寫,
            "reasoning_tokens": 推理, "estimated_cost_usd": 成本, "billing_provider": 供應商,
            "pricing_version": str(計價.get("version")),
        }, 用量事件型別)

    # ── 統計 / 列出 ────────────────────────────────────────────────────────
    def 統計工作階段(self, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """統計 session 數與用量；用量由 session_usage_events SUM 得出。"""
        from google.cloud import bigquery

        條件 = [] if include_archived else ["archived = FALSE"]
        參數: list[Any] = []
        if source:
            條件.append("source = @source")
            參數.append(bigquery.ScalarQueryParameter("source", "STRING", source))
        if user_id:
            條件.append("user_id = @user_id")
            參數.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
        where = (" WHERE " + " AND ".join(條件)) if 條件 else ""
        會話統計 = self._查詢單列(
            f"SELECT COUNT(*) AS session_count, COUNTIF(archived) AS archived_count FROM `{self._表名(會話表)}`{where}",
            參數,
        ) or {}
        用量條件 = "WHERE user_id = @user_id" if user_id else ""
        用量參數 = [bigquery.ScalarQueryParameter("user_id", "STRING", user_id)] if user_id else []
        用量統計 = self._查詢單列(
            f"""
            SELECT COUNT(*) AS api_call_count, SUM(input_tokens) AS input_tokens,
                   SUM(output_tokens) AS output_tokens, SUM(reasoning_tokens) AS reasoning_tokens,
                   SUM(estimated_cost_usd) AS estimated_cost_usd
            FROM `{self._表名(用量事件表)}` {用量條件}
            """,
            用量參數,
        ) or {}
        return {
            "session_count": int(會話統計.get("session_count") or 0),
            "archived_count": int(會話統計.get("archived_count") or 0),
            "api_call_count": int(用量統計.get("api_call_count") or 0),
            "input_tokens": int(用量統計.get("input_tokens") or 0),
            "output_tokens": int(用量統計.get("output_tokens") or 0),
            "reasoning_tokens": int(用量統計.get("reasoning_tokens") or 0),
            "estimated_cost_usd": float(用量統計.get("estimated_cost_usd") or 0.0),
            "backend": "bigquery",
            "dataset": f"{self.設定['project']}.{self.設定['dataset']}",
        }

    def 列出工作階段(self, limit: int = 20, include_children: bool = False, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """列出工作階段；預設把每條壓縮鏈投影成單一 logical conversation（最新 tip）。

        先抓近期候選（含壓縮 child），再把每條 lineage 投影到`取得壓縮Tip`、
        依 lineage root 去重，回傳 tip 列（含 `_lineage_root_id`）。
        `include_children=True` 時回傳原始列、不投影。
        這樣 `sessions list` 等顯示的是使用者實際續聊的 tip，而非壓縮前的舊 root。
        """
        from google.cloud import bigquery

        條件: list[str] = []
        參數: list[Any] = []
        if not include_archived:
            條件.append("archived = FALSE")
        if source:
            條件.append("source = @source")
            參數.append(bigquery.ScalarQueryParameter("source", "STRING", source))
        if user_id:
            條件.append("user_id = @user_id")
            參數.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
        where = (" WHERE " + " AND ".join(條件)) if 條件 else ""
        參數.append(bigquery.ScalarQueryParameter("limit", "INT64", int(max(limit * 5, limit))))
        候選清單 = self._查詢多列(
            f"SELECT * FROM `{self._表名(會話表)}`{where} ORDER BY started_at DESC, id DESC LIMIT @limit",
            參數,
        )
        if include_children:
            return 候選清單[:limit]
        投影結果: list[dict[str, Any]] = []
        已見根: set[str] = set()
        for 列 in 候選清單:
            根 = self.取得工作階段譜系(列["id"])[0]
            if 根 in 已見根:
                continue
            已見根.add(根)
            末端 = dict(self.讀取工作階段(self.取得壓縮Tip(根)) or 列)
            if not include_archived and 末端.get("archived"):
                continue
            if source and 末端.get("source") != source:
                continue
            if user_id and 末端.get("user_id") != user_id:
                continue
            末端["_lineage_root_id"] = 根
            投影結果.append(末端)
            if len(投影結果) >= limit:
                break
        return 投影結果

    # ── 續接 / 壓縮譜系 tip ────────────────────────────────────────────────
    def 解析Resume工作階段(self, 工作階段識別碼: str, user_id: str | None = None, source: str | None = None) -> str:
        """把 lineage 內的 session id 導向 compression tip。"""
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id, source=source)
        tip = self.取得壓縮Tip(工作階段識別碼)
        self.檢查工作階段存取(tip, user_id=user_id, source=source)
        return tip

    def 取得壓縮Tip(self, 工作階段識別碼: str) -> str:
        """沿 compression lineage 找到最新 active tip；無壓縮子代時回傳原 id。"""
        from google.cloud import bigquery

        目前識別碼 = 工作階段識別碼
        for _ in range(100):
            列 = self._查詢單列(
                f"""
                SELECT child.id AS id
                FROM `{self._表名(會話表)}` child
                JOIN `{self._表名(會話表)}` parent ON parent.id = child.parent_session_id
                WHERE child.parent_session_id = @pid
                  AND parent.end_reason = 'compression'
                  AND child.started_at >= COALESCE(parent.ended_at, 0)
                ORDER BY child.started_at DESC, child.id DESC LIMIT 1
                """,
                [bigquery.ScalarQueryParameter("pid", "STRING", 目前識別碼)],
            )
            if not 列:
                return 目前識別碼
            目前識別碼 = 列["id"]
        return 目前識別碼

    # ── BigQuery 查詢/寫入小工具 ───────────────────────────────────────────
    def _查詢單列(self, 語句: str, 參數: list[Any] | None = None) -> dict[str, Any] | None:
        """執行查詢並回傳第一列 dict；無資料回傳 None。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        for 列 in self.客戶端.query(語句, job_config=工作設定).result():
            return dict(列)
        return None

    def _查詢多列(self, 語句: str, 參數: list[Any] | None = None) -> list[dict[str, Any]]:
        """執行查詢並回傳所有列。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        return [dict(列) for 列 in self.客戶端.query(語句, job_config=工作設定).result()]

    def _執行DML(self, 語句: str, 參數: list[Any] | None = None) -> None:
        """執行 INSERT/UPDATE/DELETE DML。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        self.客戶端.query(語句, job_config=工作設定).result()

    def _插入單列(self, 表: str, 列: dict[str, Any], 型別對應: dict[str, str]) -> None:
        """以 DML 插入單列（依欄位型別建立參數）。"""
        from google.cloud import bigquery

        欄位 = list(列.keys())
        欄位字串 = ", ".join(欄位)
        佔位字串 = ", ".join(f"@{名}" for 名 in 欄位)
        參數 = [bigquery.ScalarQueryParameter(名, 型別對應[名], 列[名]) for 名 in 欄位]
        self._執行DML(f"INSERT INTO `{self._表名(表)}` ({欄位字串}) VALUES ({佔位字串})", 參數)

    def _插入多列(self, 表: str, 列清單: list[dict[str, Any]], 型別對應: dict[str, str]) -> None:
        """以單一 DML 插入多列（每列參數名加序號後綴以避免衝突）。"""
        from google.cloud import bigquery

        if not 列清單:
            return
        欄位 = list(列清單[0].keys())
        欄位字串 = ", ".join(欄位)
        群組: list[str] = []
        參數 = []
        for 序, 列 in enumerate(列清單):
            群組.append("(" + ", ".join(f"@{名}_{序}" for 名 in 欄位) + ")")
            參數.extend(bigquery.ScalarQueryParameter(f"{名}_{序}", 型別對應[名], 列[名]) for 名 in 欄位)
        self._執行DML(f"INSERT INTO `{self._表名(表)}` ({欄位字串}) VALUES {', '.join(群組)}", 參數)

    # ── 壓縮分裂：結束 parent、建立 child、載入壓縮後訊息 ──────────────────────
    def 建立壓縮後工作階段(self, 舊工作階段識別碼: str, 壓縮訊息清單: list[dict[str, Any]], 系統提示詞: str) -> str:
        """結束舊 session（end_reason='compression'）並建立載入壓縮後訊息的 child。

        與 SQLite 版（`工作階段庫.建立壓縮後工作階段`）語意對齊：parent 標記壓縮結束、
        child 以 parent 為 parent_session_id / compressed_from_session_id、
        compression_count+1，壓縮後訊息從 index 0 重新寫入。**parent.ended_at 與
        child.started_at 用同一時間戳**，確保 `取得壓縮Tip` 的
        `child.started_at >= COALESCE(parent.ended_at, 0)` 條件成立、resume 能導到 tip。

        BQ 與 SQLite 的差異：token 累計欄位（input/output_tokens…）已拆到
        `session_usage_events`，sessions 表沒有這些欄位，故只複製描述性欄位，不搬 token。

        參數：
            舊工作階段識別碼: 壓縮前的 session id。
            壓縮訊息清單: 壓縮後要寫入 child 的 messages。
            系統提示詞: child 要保存的 stable system prompt。
        返回值：str，新建立的 child session id。
        """
        from google.cloud import bigquery

        舊工作階段 = self.讀取工作階段(舊工作階段識別碼) or {}
        新識別碼 = f"session-{uuid.uuid4().hex[:12]}"
        目前時間 = time.time()

        # 1) 結束 parent：標記壓縮，保留既有 ended_at（重複壓縮時不覆蓋首次結束時間）
        self._執行DML(
            f"""
            UPDATE `{self._表名(會話表)}`
            SET end_reason='compression', ended_at=COALESCE(ended_at, @now), updated_at=@now
            WHERE id=@id
            """,
            [
                bigquery.ScalarQueryParameter("now", "FLOAT64", 目前時間),
                bigquery.ScalarQueryParameter("id", "STRING", 舊工作階段識別碼),
            ],
        )

        # 2) 建立 child：複製描述性欄位、接上譜系、compression_count+1
        self._插入單列(會話表, {
            "id": 新識別碼,
            "source": 舊工作階段.get("source") or "cli",
            "user_id": 舊工作階段.get("user_id"),
            "title": 舊工作階段.get("title") or 新識別碼,
            "system_prompt": 系統提示詞,
            "parent_session_id": 舊工作階段識別碼,
            "compressed_from_session_id": 舊工作階段識別碼,
            "prompt_tokens": int(舊工作階段.get("prompt_tokens") or 0),
            "compression_count": int(舊工作階段.get("compression_count") or 0) + 1,
            "model": 舊工作階段.get("model"),
            "model_config": 舊工作階段.get("model_config"),
            "cwd": 舊工作階段.get("cwd"),
            "billing_provider": 舊工作階段.get("billing_provider"),
            "billing_base_url": 舊工作階段.get("billing_base_url"),
            "billing_mode": 舊工作階段.get("billing_mode"),
            "archived": False,
            "created_at": 目前時間,
            "started_at": 目前時間,
            "updated_at": 目前時間,
        }, 會話表型別)

        # child 中繼快取，讓後續 更新模型使用量 / 建立或讀取工作階段 命中快取免再查
        self._會話中繼快取[新識別碼] = {
            "source": 舊工作階段.get("source") or "cli", "user_id": 舊工作階段.get("user_id"),
            "model": 舊工作階段.get("model"), "model_config": 舊工作階段.get("model_config"),
            "cwd": 舊工作階段.get("cwd"), "billing_provider": 舊工作階段.get("billing_provider"),
        }

        # 3) 壓縮後訊息從 index 0 重新寫入 child
        self._下一索引快取[新識別碼] = 0
        self._附加訊息清單(新識別碼, 壓縮訊息清單, 起始索引=0)
        return 新識別碼

    def 取得工作階段譜系(self, 工作階段識別碼: str) -> list[str]:
        """回傳含指定 session 的完整壓縮譜系 id 清單（root→…→tip，依時間序）。

        沿 parent_session_id 往上找到 root，再沿壓縮子代往下走到 tip，與
        `取得壓縮Tip` 用同一組 lineage 條件。

        參數：
            工作階段識別碼: 譜系內任一 session id。
        返回值：list[str]，由 root 到 tip 的 session id（至少含自身）。
        """
        from google.cloud import bigquery

        # 往上找 root（壓縮子代的 parent 一定標了 end_reason='compression'）
        root = 工作階段識別碼
        for _ in range(100):
            列 = self._查詢單列(
                f"""
                SELECT parent.id AS id
                FROM `{self._表名(會話表)}` child
                JOIN `{self._表名(會話表)}` parent ON parent.id = child.parent_session_id
                WHERE child.id = @cid AND parent.end_reason = 'compression'
                LIMIT 1
                """,
                [bigquery.ScalarQueryParameter("cid", "STRING", root)],
            )
            if not 列:
                break
            root = 列["id"]

        # 從 root 往下沿壓縮子代走到 tip，收集整條鏈
        譜系 = [root]
        目前 = root
        for _ in range(100):
            列 = self._查詢單列(
                f"""
                SELECT child.id AS id
                FROM `{self._表名(會話表)}` child
                JOIN `{self._表名(會話表)}` parent ON parent.id = child.parent_session_id
                WHERE child.parent_session_id = @pid
                  AND parent.end_reason = 'compression'
                  AND child.started_at >= COALESCE(parent.ended_at, 0)
                ORDER BY child.started_at DESC, child.id DESC LIMIT 1
                """,
                [bigquery.ScalarQueryParameter("pid", "STRING", 目前)],
            )
            if not 列:
                break
            目前 = 列["id"]
            譜系.append(目前)
        return 譜系

    # ── 封存 / 改名 ────────────────────────────────────────────────────────
    def 設定封存狀態(self, 工作階段識別碼: str, 是否封存: bool = True, user_id: str | None = None) -> None:
        """設定 session archived 狀態（UPDATE archived）。"""
        from google.cloud import bigquery

        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET archived=@a, updated_at=@now WHERE id=@id",
            [
                bigquery.ScalarQueryParameter("a", "BOOL", bool(是否封存)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
            ],
        )

    def 封存工作階段(self, 工作階段識別碼: str, user_id: str | None = None) -> None:
        """封存指定 session，讓預設列表與搜尋排除它。"""
        self.設定封存狀態(工作階段識別碼, True, user_id=user_id)

    def 取消封存工作階段(self, 工作階段識別碼: str, user_id: str | None = None) -> None:
        """取消封存指定 session。"""
        self.設定封存狀態(工作階段識別碼, False, user_id=user_id)

    def 重新命名工作階段(self, 工作階段識別碼: str, 標題: str, user_id: str | None = None) -> None:
        """更新 session title；找不到或標題為空時丟出 ValueError。"""
        from google.cloud import bigquery

        存取 = self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        if not 存取:
            raise ValueError(f"session not found: {工作階段識別碼}")
        新標題 = 標題.strip()
        if not 新標題:
            raise ValueError("title 不可為空")
        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET title=@t, updated_at=@now WHERE id=@id",
            [
                bigquery.ScalarQueryParameter("t", "STRING", 新標題),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("id", "STRING", 工作階段識別碼),
            ],
        )

    # ── 瀏覽 / 匯出（把壓縮鏈投影成單一 logical conversation）──────────────────
    def 瀏覽近期工作階段(self, limit: int = 10, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """瀏覽近期 logical sessions（`列出工作階段` 已把壓縮鏈投影到最新 tip）。"""
        工作階段清單 = self.列出工作階段(limit=limit, include_archived=include_archived, source=source, user_id=user_id)
        return {"sessions": 工作階段清單, "total_count": len(工作階段清單)}

    def 匯出工作階段JSONL(self, 輸出路徑: str | Path, limit: int = 1000, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """把 logical sessions（含壓縮譜系訊息）匯出成 JSONL。"""
        sessions = self.瀏覽近期工作階段(limit=limit, include_archived=include_archived, source=source, user_id=user_id)["sessions"]
        路徑 = Path(輸出路徑).expanduser()
        路徑.parent.mkdir(parents=True, exist_ok=True)
        訊息總數 = 0
        with 路徑.open("w", encoding="utf-8") as handle:
            for session in sessions:
                sid = str(session["id"])
                messages = self.讀取訊息(sid, include_ancestors=True, user_id=user_id)
                訊息總數 += len(messages)
                handle.write(json.dumps({"session": session, "messages": messages}, ensure_ascii=False) + "\n")
        return {"output": str(路徑), "session_count": len(sessions), "message_count": 訊息總數}

    # ── 搜尋（方案 A：LIKE 直查 + Python 端 snippet；錨點用 message_index）────────
    @staticmethod
    def _製作snippet(內容: str | None, 詞清單: list[str], 前後: int = 40) -> str:
        """在「最早命中的詞」左右各取一段字做 snippet，用 >>> <<< 標記；都找不到則取開頭。

        分詞 AND 搜尋下，命中的各詞未必相鄰，故以最早出現的那個詞為中心產生 snippet。
        """
        文字 = 內容 or ""
        小寫文字 = 文字.lower()
        最早位置, 命中詞 = -1, ""
        for 詞 in 詞清單:
            位置 = 小寫文字.find(詞.lower())
            if 位置 >= 0 and (最早位置 < 0 or 位置 < 最早位置):
                最早位置, 命中詞 = 位置, 詞
        if 最早位置 < 0:
            return 文字[:160]
        命中結束 = 最早位置 + len(命中詞)
        片段起 = max(0, 最早位置 - 前後)
        片段迄 = min(len(文字), 命中結束 + 前後)
        前綴 = "…" if 片段起 > 0 else ""
        後綴 = "…" if 片段迄 < len(文字) else ""
        return f"{前綴}{文字[片段起:最早位置]}>>>{文字[最早位置:命中結束]}<<<{文字[命中結束:片段迄]}{後綴}"

    def 搜尋訊息(self, 查詢: str, limit: int = 20, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """以 LIKE「分詞 AND」搜尋訊息內容 / 工具名 / tool_calls；回傳 id 為 message_index。

        對齊 SQLite FTS 的預設 AND 語意：多字查詢拆成各詞，每個詞都要出現（不限相鄰、
        不限順序）才算命中，而非要求整串為連續子字串；單一詞時等同單一 LIKE。
        """
        from google.cloud import bigquery

        詞清單 = 查詢.split()
        if not 詞清單:
            return []
        條件 = ["m.active = TRUE"]
        參數: list[Any] = []
        for 序號, 詞 in enumerate(詞清單):
            參數名 = f"t{序號}"
            條件.append(f"(LOWER(m.content) LIKE @{參數名} OR LOWER(m.tool_name) LIKE @{參數名} OR LOWER(m.tool_calls) LIKE @{參數名})")
            參數.append(bigquery.ScalarQueryParameter(參數名, "STRING", f"%{詞.lower()}%"))
        if not include_archived:
            條件.append("COALESCE(s.archived, FALSE) = FALSE")
        if source:
            條件.append("s.source = @source")
            參數.append(bigquery.ScalarQueryParameter("source", "STRING", source))
        if user_id:
            條件.append("s.user_id = @user_id")
            參數.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
        參數.append(bigquery.ScalarQueryParameter("lim", "INT64", int(limit)))
        列清單 = self._查詢多列(
            f"""
            SELECT m.session_id, m.message_index, m.role, m.content, m.tool_name
            FROM `{self._表名(訊息表)}` m
            JOIN `{self._表名(會話表)}` s ON s.id = m.session_id
            WHERE {' AND '.join(條件)}
            ORDER BY m.timestamp DESC, m.message_index DESC
            LIMIT @lim
            """,
            參數,
        )
        return [{
            "id": int(列["message_index"]), "session_id": 列["session_id"], "role": 列["role"],
            "content": 列["content"], "tool_name": 列["tool_name"],
            "snippet": self._製作snippet(列["content"], 詞清單),
        } for 列 in 列清單]

    def 取得錨點視圖(self, 工作階段識別碼: str, 訊息id: int, window: int = 5, bookend: int = 3) -> dict[str, Any]:
        """取得命中訊息周邊視窗與首尾 bookends；錨點以 message_index 定位。"""
        from google.cloud import bigquery

        def 查(語句: str, 額外: list[Any], 上限: int) -> list[dict[str, Any]]:
            return self._查詢多列(語句, [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼), *額外,
                                    bigquery.ScalarQueryParameter("lim", "INT64", int(上限))])

        訊息id = int(訊息id)
        錨點參數 = [bigquery.ScalarQueryParameter("idx", "INT64", 訊息id)]
        表 = self._表名(訊息表)
        前方資料列清單 = 查(f"SELECT * FROM `{表}` WHERE session_id=@sid AND message_index<=@idx AND active=TRUE ORDER BY message_index DESC LIMIT @lim", 錨點參數, window + 1)
        後方資料列清單 = 查(f"SELECT * FROM `{表}` WHERE session_id=@sid AND message_index>@idx AND active=TRUE ORDER BY message_index ASC LIMIT @lim", 錨點參數, window)
        開頭資料列清單 = 查(f"SELECT * FROM `{表}` WHERE session_id=@sid AND active=TRUE AND role IN ('user','assistant') AND LENGTH(COALESCE(content,''))>0 ORDER BY message_index ASC LIMIT @lim", [], bookend)
        結尾資料列清單 = 查(f"SELECT * FROM `{表}` WHERE session_id=@sid AND active=TRUE AND role IN ('user','assistant') AND LENGTH(COALESCE(content,''))>0 ORDER BY message_index DESC LIMIT @lim", [], bookend)
        視窗列 = list(reversed(前方資料列清單)) + list(後方資料列清單)
        轉 = lambda 列: self._資料列轉訊息(列) | {"id": int(列["message_index"])}
        return {
            "messages": [轉(列) for 列 in 視窗列],
            "messages_before": max(0, len(前方資料列清單) - 1),
            "messages_after": len(後方資料列清單),
            "bookend_start": [轉(列) for 列 in 開頭資料列清單],
            "bookend_end": [轉(列) for 列 in reversed(結尾資料列清單)],
        }

    def 搜尋工作階段(self, 查詢: str, limit: int = 3, window: int = 5, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """session_search discovery 形狀：命中後附 snippet、bookends 與命中周邊訊息，依 lineage 去重。"""
        命中清單 = self.搜尋訊息(查詢, limit=max(limit * 5, limit), include_archived=include_archived, source=source, user_id=user_id)
        結果: list[dict[str, Any]] = []
        已見: set[str] = set()
        for 命中 in 命中清單:
            命中識別碼 = 命中["session_id"]
            根識別碼 = self.取得工作階段譜系(命中識別碼)[0]
            if 根識別碼 in 已見:
                continue
            已見.add(根識別碼)
            工作階段 = self.讀取工作階段(命中識別碼) or {}
            視圖 = self.取得錨點視圖(命中識別碼, int(命中["id"]), window=window)
            結果.append({
                "session_id": 命中識別碼, "title": 工作階段.get("title"), "source": 工作階段.get("source"),
                "snippet": 命中.get("snippet"), "match_message_id": 命中.get("id"),
                "bookend_start": 視圖["bookend_start"], "messages": 視圖["messages"], "bookend_end": 視圖["bookend_end"],
                "messages_before": 視圖["messages_before"], "messages_after": 視圖["messages_after"],
                "_lineage_root_id": 根識別碼,
            })
            if len(結果) >= limit:
                break
        return 結果

    def 讀取工作階段全文(self, 工作階段識別碼: str, user_id: str | None = None) -> dict[str, Any]:
        """讀取整段 session；訊息過多時回傳首 20 + 末 10 摘要。訊息 id 為 message_index。"""
        from google.cloud import bigquery

        存取 = self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        if not 存取:
            raise ValueError(f"session not found: {工作階段識別碼}")
        工作階段 = self.讀取工作階段(工作階段識別碼) or dict(存取)
        列清單 = self._查詢多列(
            f"SELECT * FROM `{self._表名(訊息表)}` WHERE session_id=@sid AND active=TRUE ORDER BY message_index",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        總訊息數 = len(列清單)
        保留 = (列清單[:20] + 列清單[-10:]) if 總訊息數 > 35 else 列清單
        return {
            "session_id": 工作階段識別碼,
            "session": 工作階段,
            "messages": [self._資料列轉訊息(列) | {"id": int(列["message_index"])} for 列 in 保留],
            "total_messages": 總訊息數,
            "truncated": 總訊息數 > 35,
        }

    def 捲動工作階段訊息(self, 工作階段識別碼: str, around_message_id: int, window: int = 5, user_id: str | None = None) -> dict[str, Any]:
        """讀取 anchor（message_index）周邊視窗，供 session_search scroll 形狀使用。"""
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        視圖 = self.取得錨點視圖(工作階段識別碼, int(around_message_id), window=window, bookend=0)
        return {
            "session_id": 工作階段識別碼,
            "around_message_id": int(around_message_id),
            "messages": 視圖["messages"],
            "messages_before": 視圖["messages_before"],
            "messages_after": 視圖["messages_after"],
        }

    # ── rewind / soft-delete 重寫（錨點用 message_index）──────────────────────
    def 替換訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]]) -> None:
        """以 active=FALSE soft-delete 舊 messages 後 append 新 transcript（從 index 0）。"""
        from google.cloud import bigquery

        self._執行DML(
            f"UPDATE `{self._表名(訊息表)}` SET active=FALSE WHERE session_id=@sid AND active=TRUE",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET rewind_count=COALESCE(rewind_count,0)+1, updated_at=@now WHERE id=@sid",
            [bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
             bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        self._下一索引快取[工作階段識別碼] = 0
        self._附加訊息清單(工作階段識別碼, 訊息清單, 起始索引=0)

    def 取得最後作用中User訊息(self, 工作階段識別碼: str, user_id: str | None = None) -> dict[str, Any] | None:
        """讀取目前 session 最後一則 active user message（供 /retry、/undo 用）。

        BQ 無共用連線、不需鎖；`id` 回傳 `message_index`（即 rewind到訊息 需要的錨點）。

        參數：
            工作階段識別碼: session id。
            user_id: 可選使用者 scope；提供時會拒絕跨使用者讀取。
        返回值：dict | None，含 `id`（message_index）與 `content`；無則 None。
        """
        from google.cloud import bigquery

        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        列 = self._查詢單列(
            f"SELECT message_index, content FROM `{self._表名(訊息表)}` "
            f"WHERE session_id=@sid AND active=TRUE AND role='user' ORDER BY message_index DESC LIMIT 1",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        return {"id": int(列["message_index"]), "content": 列.get("content")} if 列 else None

    def rewind到訊息(self, 工作階段識別碼: str, 目標訊息id: int, user_id: str | None = None) -> dict[str, Any]:
        """把 message_index >= 目標的 active 訊息標記為 inactive（保留 audit）。目標訊息id 為 message_index。"""
        from google.cloud import bigquery

        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        目標索引 = int(目標訊息id)
        目標列 = self._查詢單列(
            f"SELECT * FROM `{self._表名(訊息表)}` WHERE session_id=@sid AND message_index=@idx AND active=TRUE LIMIT 1",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼),
             bigquery.ScalarQueryParameter("idx", "INT64", 目標索引)],
        )
        if not 目標列:
            raise ValueError(f"message {目標訊息id} not found in session {工作階段識別碼}")
        計數列 = self._查詢單列(
            f"SELECT COUNT(*) AS c FROM `{self._表名(訊息表)}` WHERE session_id=@sid AND message_index>=@idx AND active=TRUE",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼),
             bigquery.ScalarQueryParameter("idx", "INT64", 目標索引)],
        )
        停用數 = int((計數列 or {}).get("c") or 0)
        self._執行DML(
            f"UPDATE `{self._表名(訊息表)}` SET active=FALSE WHERE session_id=@sid AND message_index>=@idx AND active=TRUE",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼),
             bigquery.ScalarQueryParameter("idx", "INT64", 目標索引)],
        )
        self._執行DML(
            f"UPDATE `{self._表名(會話表)}` SET rewind_count=COALESCE(rewind_count,0)+1, updated_at=@now WHERE id=@sid",
            [bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
             bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        新前端列 = self._查詢單列(
            f"SELECT MAX(message_index) AS m FROM `{self._表名(訊息表)}` WHERE session_id=@sid AND active=TRUE",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
        )
        新前端 = (新前端列 or {}).get("m")
        self._下一索引快取[工作階段識別碼] = (int(新前端) if 新前端 is not None else -1) + 1
        return {
            "rewound_count": 停用數,
            "target_message": self._資料列轉訊息(目標列),
            "new_head_id": int(新前端) if 新前端 is not None else None,
        }


# 會話表欄位型別（供 _插入單列 建立參數）
會話表型別 = {
    "id": "STRING", "source": "STRING", "user_id": "STRING", "model": "STRING",
    "model_config": "STRING", "system_prompt": "STRING", "cwd": "STRING", "title": "STRING",
    "parent_session_id": "STRING", "compressed_from_session_id": "STRING", "end_reason": "STRING",
    "prompt_tokens": "INT64", "compression_count": "INT64", "rewind_count": "INT64",
    "billing_provider": "STRING", "billing_base_url": "STRING", "billing_mode": "STRING",
    "handoff_state": "STRING", "handoff_platform": "STRING", "handoff_error": "STRING",
    "archived": "BOOL", "created_at": "FLOAT64", "updated_at": "FLOAT64",
    "started_at": "FLOAT64", "ended_at": "FLOAT64",
}


def _轉JSON(值: Any) -> str | None:
    """把 dict/list 轉成 JSON 字串供 STRING 欄位保存。"""
    if not 值:
        return None
    return json.dumps(值, ensure_ascii=False)


def _估算成本(供應商: str, 模型: str | None, 輸入Token: int, 輸出Token: int) -> tuple[dict[str, Any], float]:
    """依本地價格表估算單次成本，沿用本機版價格設定。"""
    from .工作階段庫 import 每百萬Token價格表, 預設每百萬Token價格

    計價 = 每百萬Token價格表.get((供應商, 模型 or "")) or 每百萬Token價格表.get(("gemini-adc", 模型 or "")) or 預設每百萬Token價格
    成本 = (輸入Token * float(計價["input"]) + 輸出Token * float(計價["output"])) / 1_000_000
    return dict(計價), 成本
