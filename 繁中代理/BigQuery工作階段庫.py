"""BigQuery 版工作階段庫（雲端儲存後端）。

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
from .管理部_bigquery import 載入本機環境檔, 檢查資源名稱

預設資料集 = "agent_core"
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


def 應跳過建表() -> bool:
    """讀 CORE_BQ_SKIP_DDL；為真時跳過 CREATE TABLE（表已存在時加速啟動）。"""
    載入本機環境檔()
    return os.getenv("CORE_BQ_SKIP_DDL", "").strip().lower() in {"1", "true", "yes"}


def 讀取核心BigQuery設定() -> dict[str, str | None]:
    """從環境變數讀取核心資料的 BigQuery 設定。"""
    載入本機環境檔()
    專案 = os.getenv("CORE_BQ_PROJECT", "").strip()
    if not 專案:
        raise ValueError("BigQuery 儲存後端尚未設定：缺少 CORE_BQ_PROJECT")
    資料集 = os.getenv("CORE_BQ_DATASET", 預設資料集).strip() or 預設資料集
    for 名稱, 值 in [("project", 專案), ("dataset", 資料集)]:
        檢查資源名稱(值, 名稱)
    return {
        "project": 專案,
        "dataset": 資料集,
        "location": os.getenv("CORE_BQ_LOCATION", "").strip() or None,
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
        啟用條件 = "" if 包含停用 else " AND active = TRUE"
        列清單 = self._查詢多列(
            f"SELECT * FROM `{self._表名(訊息表)}` WHERE session_id=@sid{啟用條件} ORDER BY message_index, timestamp",
            [bigquery.ScalarQueryParameter("sid", "STRING", 工作階段識別碼)],
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
        """列出工作階段；預設排除封存與壓縮子 session。"""
        from google.cloud import bigquery

        條件: list[str] = []
        參數: list[Any] = []
        if not include_archived:
            條件.append("archived = FALSE")
        if not include_children:
            條件.append("parent_session_id IS NULL")
        if source:
            條件.append("source = @source")
            參數.append(bigquery.ScalarQueryParameter("source", "STRING", source))
        if user_id:
            條件.append("user_id = @user_id")
            參數.append(bigquery.ScalarQueryParameter("user_id", "STRING", user_id))
        where = (" WHERE " + " AND ".join(條件)) if 條件 else ""
        參數.append(bigquery.ScalarQueryParameter("limit", "INT64", int(limit)))
        return self._查詢多列(
            f"SELECT * FROM `{self._表名(會話表)}`{where} ORDER BY started_at DESC, id DESC LIMIT @limit",
            參數,
        )

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

    # ── 尚未實作（後續：壓縮譜系、搜尋、封存、rewind、匯出等）─────────────────
    def _尚未實作(self, 名稱: str):
        """統一丟出「BigQuery 後端尚未實作此方法」錯誤。"""
        raise NotImplementedError(f"BigQuery 工作階段庫尚未實作：{名稱}（後續階段補上）")

    def 替換訊息清單(self, *參數, **關鍵字):
        """尚未實作：rewind soft-delete 重寫 transcript。"""
        return self._尚未實作("替換訊息清單")

    def 建立壓縮後工作階段(self, *參數, **關鍵字):
        """尚未實作：建立 compression child session。"""
        return self._尚未實作("建立壓縮後工作階段")

    def 取得工作階段譜系(self, *參數, **關鍵字):
        """尚未實作：取得 compression lineage。"""
        return self._尚未實作("取得工作階段譜系")

    def 搜尋訊息(self, *參數, **關鍵字):
        """尚未實作：訊息全文搜尋。"""
        return self._尚未實作("搜尋訊息")

    def 搜尋工作階段(self, *參數, **關鍵字):
        """尚未實作：工作階段全文搜尋。"""
        return self._尚未實作("搜尋工作階段")

    def 讀取工作階段全文(self, *參數, **關鍵字):
        """尚未實作：讀取整段 session 全文。"""
        return self._尚未實作("讀取工作階段全文")

    def 捲動工作階段訊息(self, *參數, **關鍵字):
        """尚未實作：以錨點捲動訊息視窗。"""
        return self._尚未實作("捲動工作階段訊息")

    def 瀏覽近期工作階段(self, *參數, **關鍵字):
        """尚未實作：瀏覽近期 sessions。"""
        return self._尚未實作("瀏覽近期工作階段")

    def 取得錨點視圖(self, *參數, **關鍵字):
        """尚未實作：取得訊息錨點視圖。"""
        return self._尚未實作("取得錨點視圖")

    def 重新命名工作階段(self, *參數, **關鍵字):
        """尚未實作：重新命名 session。"""
        return self._尚未實作("重新命名工作階段")

    def 設定封存狀態(self, *參數, **關鍵字):
        """尚未實作：設定封存狀態。"""
        return self._尚未實作("設定封存狀態")

    def 封存工作階段(self, *參數, **關鍵字):
        """尚未實作：封存 session。"""
        return self._尚未實作("封存工作階段")

    def 取消封存工作階段(self, *參數, **關鍵字):
        """尚未實作：取消封存 session。"""
        return self._尚未實作("取消封存工作階段")

    def rewind到訊息(self, *參數, **關鍵字):
        """尚未實作：rewind 到指定訊息。"""
        return self._尚未實作("rewind到訊息")

    def 匯出工作階段JSONL(self, *參數, **關鍵字):
        """尚未實作：匯出 sessions 為 JSONL。"""
        return self._尚未實作("匯出工作階段JSONL")


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
