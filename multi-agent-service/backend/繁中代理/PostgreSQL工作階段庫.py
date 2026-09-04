"""PostgreSQL sessions/messages/usage/lineage/compression repository。

Runtime 只執行 DML；schema 必須由部署 migration 預先建立。所有交易都使用
``PostgreSQL連線.交易連線``，不會 fallback 至 SQLite/BigQuery。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from psycopg.types.json import Jsonb

from .PostgreSQL連線 import 交易連線
from .環境設定 import 交易儲存設定
from .工作階段庫 import 每百萬Token價格表, 預設每百萬Token價格


class PostgreSQL工作階段庫:
    """以 PostgreSQL 保存核心工作階段狀態；建構與 import 均不執行 DDL。"""

    def __init__(self, 凍結設定: 交易儲存設定) -> None:
        if type(凍結設定) is not 交易儲存設定 or 凍結設定.後端 != "postgres":
            raise ValueError("PostgreSQL 工作階段庫設定無效")
        self.凍結設定 = 凍結設定

    @contextmanager
    def _交易(self) -> Iterator[Any]:
        with 交易連線(self.凍結設定) as 連線:
            yield 連線

    @staticmethod
    def _一列(結果: Any) -> dict[str, Any] | None:
        列 = 結果.fetchone()
        return dict(列) if 列 is not None else None

    @staticmethod
    def _多列(結果: Any) -> list[dict[str, Any]]:
        return [dict(列) for 列 in 結果.fetchall()]

    @staticmethod
    def _現在() -> datetime:
        """回傳可安全綁定 PostgreSQL ``timestamptz`` 的 UTC 時間。"""
        return datetime.now(timezone.utc)

    @staticmethod
    def _JSON值(值: Any) -> Any:
        """以 psycopg 明確 JSONB adapter 綁定 Python 值。"""
        return Jsonb(值) if 值 is not None else None

    @staticmethod
    def _解JSON(值: Any, 預設: Any = None) -> Any:
        if 值 is None:
            return 預設
        if isinstance(值, (dict, list, int, float, bool)):
            return 值
        try:
            return json.loads(值)
        except (TypeError, json.JSONDecodeError):
            return 預設

    def 建立或讀取工作階段(self, 工作階段識別碼: str | None = None,
                         parent_session_id: str | None = None, source: str = "cli",
                         user_id: str | None = None, model: str | None = None,
                         model_config: dict[str, Any] | None = None,
                         cwd: str | None = None) -> str:
        識別碼 = 工作階段識別碼 or f"session-{uuid.uuid4().hex[:12]}"
        現在 = self._現在()
        模型設定 = self._JSON值(model_config)
        with self._交易() as 連線:
            現有 = self._一列(連線.execute(
                "SELECT user_id FROM sessions WHERE id=%s FOR UPDATE", (識別碼,)))
            if 現有 and user_id and 現有.get("user_id") and 現有["user_id"] != user_id:
                raise PermissionError(f"使用者 {user_id} 無權接管 session {識別碼}")
            if 現有 is None:
                連線.execute("""
                    INSERT INTO sessions
                    (id,source,user_id,title,parent_session_id,model,model_config,cwd,
                     archived,created_at,started_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s)
                """, (識別碼, source, user_id, 識別碼, parent_session_id, model,
                      模型設定, cwd, 現在, 現在, 現在))
            else:
                連線.execute("""
                    UPDATE sessions SET source=COALESCE(%s,source),
                    user_id=COALESCE(%s,user_id), model=COALESCE(%s,model),
                    model_config=COALESCE(%s,model_config), cwd=COALESCE(%s,cwd),
                    updated_at=%s WHERE id=%s
                """, (source, user_id, model, 模型設定, cwd, 現在, 識別碼))
        return 識別碼

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        with self._交易() as 連線:
            return self._一列(連線.execute(
                "SELECT * FROM sessions WHERE id=%s LIMIT 1", (工作階段識別碼,)))

    def 檢查工作階段存取(self, 工作階段識別碼: str, user_id: str | None = None,
                       source: str | None = None) -> dict[str, Any] | None:
        工作階段 = self.讀取工作階段(工作階段識別碼)
        if not 工作階段:
            return None
        if user_id and 工作階段.get("user_id") and 工作階段["user_id"] != user_id:
            raise PermissionError(f"使用者 {user_id} 無權存取 session {工作階段識別碼}")
        if source and 工作階段.get("source") and 工作階段["source"] != source:
            raise PermissionError(f"來源 {source} 無權存取 session {工作階段識別碼}")
        return 工作階段

    def 更新系統提示詞(self, 工作階段識別碼: str, 系統提示詞: str) -> None:
        with self._交易() as 連線:
            連線.execute("UPDATE sessions SET system_prompt=%s,updated_at=%s WHERE id=%s",
                       (系統提示詞, self._現在(), 工作階段識別碼))

    def 更新提示Token數(self, 工作階段識別碼: str, token數: int) -> None:
        with self._交易() as 連線:
            連線.execute("UPDATE sessions SET prompt_tokens=%s,updated_at=%s WHERE id=%s",
                       (int(token數), self._現在(), 工作階段識別碼))

    @staticmethod
    def _準備訊息列(工作階段識別碼: str, 訊息: dict[str, Any], 索引: int,
                 現在: datetime) -> tuple[Any, ...]:
        內容 = 訊息.get("content")
        內容字串 = 內容 if isinstance(內容, str) or 內容 is None else json.dumps(內容, ensure_ascii=False)
        工具呼叫 = 訊息.get("tool_calls")
        工具名 = 訊息.get("name") or 訊息.get("tool_name")
        if not 工具名 and isinstance(工具呼叫, list) and 工具呼叫:
            工具名 = (工具呼叫[0].get("function") or {}).get("name")
        轉JSON = lambda 值: Jsonb(值) if 值 is not None else None
        return (
            工作階段識別碼, 索引, str(訊息.get("role", "")),
            內容字串, Jsonb(訊息), 訊息.get("tool_call_id"),
            轉JSON(工具呼叫), 工具名, 訊息.get("token_count"), 訊息.get("finish_reason"),
            訊息.get("reasoning"), 訊息.get("reasoning_content"),
            轉JSON(訊息.get("reasoning_details")), 轉JSON(訊息.get("codex_reasoning_items")),
            轉JSON(訊息.get("codex_message_items")),
            訊息.get("platform_message_id") or 訊息.get("message_id"),
            bool(訊息.get("observed")), 現在, 現在,
        )

    def _附加訊息清單(self, 連線: Any, 工作階段識別碼: str,
                 訊息清單: list[dict[str, Any]], 起始索引: int) -> list[int]:
        ids: list[int] = []
        現在 = self._現在()
        for 偏移, 訊息 in enumerate(訊息清單):
            列 = self._準備訊息列(工作階段識別碼, 訊息, 起始索引 + 偏移,
                            現在 + timedelta(microseconds=偏移))
            寫入結果 = 連線.execute("""
                INSERT INTO messages
                (session_id,message_index,role,content,content_json,tool_call_id,
                 tool_calls,tool_name,token_count,finish_reason,reasoning,reasoning_content,
                 reasoning_details,codex_reasoning_items,codex_message_items,
                 platform_message_id,observed,active,created_at,timestamp)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s)
                RETURNING id
            """, 列)
            已寫入 = self._一列(寫入結果)
            if 已寫入 is not None:
                ids.append(int(已寫入["id"]))
        工具呼叫增量 = 0
        for 訊息 in 訊息清單:
            if 訊息.get("tool_call_id"):
                工具呼叫增量 += 1
            else:
                工具呼叫 = 訊息.get("tool_calls")
                if 工具呼叫:
                    工具呼叫增量 += len(工具呼叫) if isinstance(工具呼叫, list) else 1
        連線.execute(
            "UPDATE sessions SET message_count=message_count+%s,"
            "tool_call_count=tool_call_count+%s,updated_at=%s WHERE id=%s",
            (len(ids), 工具呼叫增量, self._現在(), 工作階段識別碼),
        )
        return ids

    def _下一個訊息索引(self, 連線: Any, 工作階段識別碼: str) -> int:
        # session row lock serializes appenders, preventing duplicate message_index values.
        if self._一列(連線.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE",
                                (工作階段識別碼,))) is None:
            raise ValueError(f"session not found: {工作階段識別碼}")
        列 = self._一列(連線.execute(
            "SELECT MAX(message_index) AS m FROM messages WHERE session_id=%s AND active=TRUE",
            (工作階段識別碼,))) or {}
        return int(列["m"] if 列.get("m") is not None else -1) + 1

    def 附加單一訊息(self, 工作階段識別碼: str, 訊息: dict[str, Any]) -> int:
        with self._交易() as 連線:
            索引 = self._下一個訊息索引(連線, 工作階段識別碼)
            self._附加訊息清單(連線, 工作階段識別碼, [訊息], 索引)
            return 索引

    def 寫入訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]],
                 是否使用既有交易: bool = False) -> None:
        # PostgreSQL repository owns transaction boundaries; legacy flag is accepted only for API parity.
        with self._交易() as 連線:
            起始 = self._下一個訊息索引(連線, 工作階段識別碼)
            if 起始 < len(訊息清單):
                self._附加訊息清單(連線, 工作階段識別碼, 訊息清單[起始:], 起始)
            else:
                連線.execute("UPDATE sessions SET updated_at=%s WHERE id=%s",
                           (self._現在(), 工作階段識別碼))

    def 讀取訊息(self, 工作階段識別碼: str, 包含停用: bool = False,
             include_ancestors: bool = False, user_id: str | None = None) -> list[dict[str, Any]]:
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        ids = self.取得工作階段譜系(工作階段識別碼) if include_ancestors else [工作階段識別碼]
        with self._交易() as 連線:
            列清單 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=ANY(%s)" +
                ("" if 包含停用 else " AND active=TRUE") +
                " ORDER BY timestamp,message_index,id", (ids,)))
        return [self._資料列轉訊息(列) for 列 in 列清單]

    @staticmethod
    def _資料列轉訊息(列: dict[str, Any]) -> dict[str, Any]:
        原始訊息 = PostgreSQL工作階段庫._解JSON(列.get("content_json"), {})
        訊息 = dict(原始訊息) if isinstance(原始訊息, dict) else {}
        訊息["role"] = 列.get("role")
        if 列.get("content") is not None:
            訊息["content"] = 列["content"]
        對應 = {"tool_call_id": "tool_call_id", "finish_reason": "finish_reason",
              "token_count": "token_count", "reasoning": "reasoning",
              "reasoning_content": "reasoning_content"}
        for 欄位, 目標 in 對應.items():
            if 列.get(欄位) is not None:
                訊息[目標] = 列[欄位]
        if 列.get("tool_name"):
            訊息["name"] = 訊息["tool_name"] = 列["tool_name"]
        for 欄位 in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if 列.get(欄位) is not None:
                訊息[欄位] = PostgreSQL工作階段庫._解JSON(列[欄位], 列[欄位])
        if 列.get("platform_message_id"):
            訊息["message_id"] = 訊息["platform_message_id"] = 列["platform_message_id"]
        if 列.get("observed"):
            訊息["observed"] = True
        return 訊息

    def 更新模型使用量(self, 工作階段識別碼: str, 使用量: dict[str, Any] | None,
                 api呼叫增量: int = 1, billing_provider: str | None = None) -> None:
        使用量 = 使用量 or {}
        輸入 = int(使用量.get("input_tokens") or 使用量.get("prompt_tokens") or 使用量.get("prompt_token_count") or 0)
        輸出 = int(使用量.get("output_tokens") or 使用量.get("completion_tokens") or 使用量.get("candidates_token_count") or 0)
        快取讀 = int(使用量.get("cache_read_tokens") or 使用量.get("cached_content_token_count") or 0)
        快取寫 = int(使用量.get("cache_write_tokens") or 0)
        推理 = int(使用量.get("reasoning_tokens") or 使用量.get("thoughts_token_count") or 0)
        if any(value < 0 for value in (輸入, 輸出, 快取讀, 快取寫, 推理)):
            raise ValueError("usage token 不可為負數")
        if type(api呼叫增量) is not int or api呼叫增量 < 0:
            raise ValueError("api呼叫增量不可為負數")
        with self._交易() as 連線:
            會話 = self._一列(連線.execute(
                "SELECT user_id,model,billing_provider FROM sessions WHERE id=%s",
                (工作階段識別碼,)))
            if not 會話:
                raise ValueError(f"session not found: {工作階段識別碼}")
            供應商 = billing_provider or 會話.get("billing_provider") or "unknown"
            計價 = (每百萬Token價格表.get((供應商, 會話.get("model") or "")) or
                  每百萬Token價格表.get(("gemini-adc", 會話.get("model") or "")) or 預設每百萬Token價格)
            成本 = (輸入 * float(計價["input"]) + 輸出 * float(計價["output"])) / 1_000_000
            # One row per provider call. api呼叫增量=0 deliberately records no event.
            for _ in range(max(0, int(api呼叫增量))):
                連線.execute("""
                    INSERT INTO session_usage_events
                    (id,session_id,user_id,created_at,model,prompt_tokens,input_tokens,
                     output_tokens,cache_read_tokens,cache_write_tokens,reasoning_tokens,
                     estimated_cost_usd,metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (uuid.uuid4().hex, 工作階段識別碼, 會話.get("user_id"), self._現在(),
                      會話.get("model"), 輸入, 輸入, 輸出, 快取讀, 快取寫, 推理, 成本,
                      Jsonb({"billing_provider": 供應商, "pricing_version": str(計價["version"])})))
            增量 = max(0, int(api呼叫增量))
            if 增量:
                連線.execute("""
                    UPDATE sessions SET input_tokens=input_tokens+%s,output_tokens=output_tokens+%s,
                    cache_read_tokens=cache_read_tokens+%s,cache_write_tokens=cache_write_tokens+%s,
                    reasoning_tokens=reasoning_tokens+%s,api_call_count=api_call_count+%s,
                    estimated_cost_usd=COALESCE(estimated_cost_usd,0)+%s,
                    billing_provider=%s,pricing_version=%s,updated_at=%s WHERE id=%s
                """, (輸入 * 增量, 輸出 * 增量, 快取讀 * 增量, 快取寫 * 增量,
                      推理 * 增量, 增量, 成本 * 增量, 供應商, str(計價["version"]),
                      self._現在(), 工作階段識別碼))

    def 取得工作階段譜系(self, 工作階段識別碼: str) -> list[str]:
        with self._交易() as 連線:
            列 = self._一列(連線.execute("""
                WITH RECURSIVE lineage AS (
                  SELECT id,parent_session_id,0 AS depth FROM sessions WHERE id=%s
                  UNION ALL
                  SELECT p.id,p.parent_session_id,l.depth+1 FROM sessions p
                  JOIN lineage l ON p.id=l.parent_session_id WHERE l.depth < 99
                )
                SELECT ARRAY_AGG(id ORDER BY depth DESC) AS ids FROM lineage
            """, (工作階段識別碼,))) or {}
        return list(列.get("ids") or [工作階段識別碼])

    def 取得壓縮Tip(self, 工作階段識別碼: str) -> str:
        with self._交易() as 連線:
            列 = self._一列(連線.execute("""
                WITH RECURSIVE tips AS (
                  SELECT id,0 AS depth FROM sessions WHERE id=%s
                  UNION ALL
                  SELECT c.id,t.depth+1 FROM sessions c JOIN tips t ON c.parent_session_id=t.id
                  JOIN sessions p ON p.id=t.id
                  WHERE p.end_reason='compression'
                    AND c.started_at>=COALESCE(p.ended_at,p.started_at,p.created_at)
                    AND t.depth < 99
                ) SELECT id FROM tips ORDER BY depth DESC,id DESC LIMIT 1
            """, (工作階段識別碼,)))
        return str(列["id"]) if 列 else 工作階段識別碼

    def 解析Resume工作階段(self, 工作階段識別碼: str, user_id: str | None = None,
                       source: str | None = None) -> str:
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id, source=source)
        tip = self.取得壓縮Tip(工作階段識別碼)
        self.檢查工作階段存取(tip, user_id=user_id, source=source)
        return tip

    def 建立壓縮後工作階段(self, 舊工作階段識別碼: str,
                       壓縮訊息清單: list[dict[str, Any]], 系統提示詞: str) -> str:
        新識別碼 = f"session-{uuid.uuid4().hex[:12]}"
        現在 = self._現在()
        with self._交易() as 連線:
            parent = self._一列(連線.execute(
                "SELECT * FROM sessions WHERE id=%s FOR UPDATE", (舊工作階段識別碼,)))
            if not parent:
                raise ValueError(f"session not found: {舊工作階段識別碼}")
            if parent.get("end_reason") == "compression":
                child = self._一列(連線.execute(
                    "SELECT id FROM sessions WHERE parent_session_id=%s ORDER BY started_at DESC,id DESC LIMIT 1",
                    (舊工作階段識別碼,)))
                if child:
                    return str(child["id"])
            連線.execute("UPDATE sessions SET end_reason='compression',ended_at=COALESCE(ended_at,%s),updated_at=%s WHERE id=%s",
                       (現在, 現在, 舊工作階段識別碼))
            連線.execute("""
                INSERT INTO sessions
                (id,source,user_id,title,system_prompt,parent_session_id,compressed_from_session_id,
                 prompt_tokens,compression_count,model,model_config,cwd,billing_provider,
                 billing_base_url,billing_mode,archived,created_at,started_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE,%s,%s,%s)
            """, (新識別碼, parent.get("source") or "cli", parent.get("user_id"),
                  parent.get("title") or 新識別碼, 系統提示詞, 舊工作階段識別碼,
                  舊工作階段識別碼, int(parent.get("prompt_tokens") or 0),
                  int(parent.get("compression_count") or 0)+1, parent.get("model"),
                  self._JSON值(parent.get("model_config")), parent.get("cwd"), parent.get("billing_provider"),
                  parent.get("billing_base_url"), parent.get("billing_mode"), 現在, 現在, 現在))
            self._附加訊息清單(連線, 新識別碼, 壓縮訊息清單, 0)
        return 新識別碼

    def 建立壓縮鎖Holder(self, agent標籤: str | None = None) -> str:
        return f"pid={os.getpid()}:tid={threading.get_ident()}:agent={agent標籤 or id(self):x}:nonce={uuid.uuid4().hex[:8]}"

    def 取得壓縮鎖(self, 工作階段識別碼: str, 擁有者: str | None = None,
               ttl秒: int = 300) -> str | None:
        擁有者 = 擁有者 or self.建立壓縮鎖Holder()
        現在 = self._現在()
        過期 = 現在 + timedelta(seconds=int(ttl秒))
        with self._交易() as 連線:
            列 = self._一列(連線.execute("""
                INSERT INTO compression_leases(session_id,holder,acquired_at,expires_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (session_id) DO UPDATE SET holder=EXCLUDED.holder,
                  acquired_at=EXCLUDED.acquired_at,expires_at=EXCLUDED.expires_at
                WHERE compression_leases.expires_at < %s OR compression_leases.holder=EXCLUDED.holder
                RETURNING holder
            """, (工作階段識別碼, 擁有者, 現在, 過期, 現在)))
        return 擁有者 if 列 and 列.get("holder") == 擁有者 else None

    def 讀取壓縮鎖Holder(self, 工作階段識別碼: str) -> str | None:
        with self._交易() as 連線:
            列 = self._一列(連線.execute(
                "SELECT holder FROM compression_leases WHERE session_id=%s AND expires_at>=%s",
                (工作階段識別碼, self._現在())))
        return str(列["holder"]) if 列 else None

    def 釋放壓縮鎖(self, 工作階段識別碼: str, 擁有者: str) -> None:
        with self._交易() as 連線:
            連線.execute("DELETE FROM compression_leases WHERE session_id=%s AND holder=%s",
                       (工作階段識別碼, 擁有者))

    @contextmanager
    def 壓縮鎖(self, 工作階段識別碼: str, ttl秒: int = 300) -> Iterator[bool]:
        holder = self.取得壓縮鎖(工作階段識別碼, ttl秒=ttl秒)
        try:
            yield holder is not None
        finally:
            if holder:
                self.釋放壓縮鎖(工作階段識別碼, holder)

    def 讀取首則使用者訊息(self, 工作階段識別碼: str,
                     user_id: str | None = None) -> str | None:
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        ids = self.取得工作階段譜系(工作階段識別碼)
        with self._交易() as 連線:
            列 = self._一列(連線.execute(
                "SELECT content FROM messages WHERE session_id=ANY(%s) AND active=TRUE "
                "AND role='user' ORDER BY timestamp,id LIMIT 1", (ids,)))
        return str(列["content"]) if 列 and 列.get("content") is not None else None

    def 替換訊息清單(self, 工作階段識別碼: str,
                 訊息清單: list[dict[str, Any]]) -> None:
        with self._交易() as 連線:
            if self._一列(連線.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE",
                                    (工作階段識別碼,))) is None:
                raise ValueError(f"session not found: {工作階段識別碼}")
            連線.execute("UPDATE messages SET active=FALSE WHERE session_id=%s AND active=TRUE",
                       (工作階段識別碼,))
            連線.execute("UPDATE sessions SET message_count=0,tool_call_count=0,"
                         "rewind_count=COALESCE(rewind_count,0)+1 WHERE id=%s",
                       (工作階段識別碼,))
            self._附加訊息清單(連線, 工作階段識別碼, 訊息清單, 0)

    def 取得最後作用中User訊息(self, 工作階段識別碼: str,
                         user_id: str | None = None) -> dict[str, Any] | None:
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        with self._交易() as 連線:
            列 = self._一列(連線.execute(
                "SELECT message_index AS id,content FROM messages WHERE session_id=%s "
                "AND active=TRUE AND role='user' ORDER BY message_index DESC LIMIT 1",
                (工作階段識別碼,)))
        return 列

    def rewind到訊息(self, 工作階段識別碼: str, 目標訊息id: int,
                user_id: str | None = None) -> dict[str, Any]:
        self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        with self._交易() as 連線:
            連線.execute("SELECT id FROM sessions WHERE id=%s FOR UPDATE", (工作階段識別碼,))
            目標 = self._一列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND message_index=%s AND active=TRUE",
                (工作階段識別碼, int(目標訊息id))))
            if not 目標:
                raise ValueError(f"message {目標訊息id} not found in session {工作階段識別碼}")
            計數 = self._一列(連線.execute(
                "SELECT COUNT(*) AS c,COALESCE(SUM(CASE WHEN tool_call_id IS NOT NULL THEN 1 "
                "WHEN jsonb_typeof(tool_calls)='array' THEN jsonb_array_length(tool_calls) "
                "WHEN tool_calls IS NOT NULL THEN 1 ELSE 0 END),0) AS tc "
                "FROM messages WHERE session_id=%s AND message_index>=%s AND active=TRUE",
                (工作階段識別碼, int(目標訊息id)))) or {}
            連線.execute("UPDATE messages SET active=FALSE WHERE session_id=%s AND message_index>=%s AND active=TRUE",
                       (工作階段識別碼, int(目標訊息id)))
            連線.execute("UPDATE sessions SET message_count=GREATEST(0,message_count-%s),"
                         "tool_call_count=GREATEST(0,tool_call_count-%s),"
                         "rewind_count=COALESCE(rewind_count,0)+1,updated_at=%s WHERE id=%s",
                       (int(計數.get("c") or 0), int(計數.get("tc") or 0),
                        self._現在(), 工作階段識別碼))
            head = self._一列(連線.execute(
                "SELECT MAX(message_index) AS m FROM messages WHERE session_id=%s AND active=TRUE",
                (工作階段識別碼,))) or {}
        return {"rewound_count": int(計數.get("c") or 0),
                "target_message": self._資料列轉訊息(目標), "new_head_id": head.get("m")}

    def 設定封存狀態(self, 工作階段識別碼: str, 是否封存: bool = True,
                 user_id: str | None = None) -> None:
        if not self.檢查工作階段存取(工作階段識別碼, user_id=user_id):
            raise ValueError(f"session not found: {工作階段識別碼}")
        with self._交易() as 連線:
            連線.execute("UPDATE sessions SET archived=%s,updated_at=%s WHERE id=%s",
                       (bool(是否封存), self._現在(), 工作階段識別碼))

    def 封存工作階段(self, 工作階段識別碼: str, user_id: str | None = None) -> None:
        self.設定封存狀態(工作階段識別碼, True, user_id=user_id)

    def 取消封存工作階段(self, 工作階段識別碼: str, user_id: str | None = None) -> None:
        self.設定封存狀態(工作階段識別碼, False, user_id=user_id)

    def 重新命名工作階段(self, 工作階段識別碼: str, 標題: str,
                    user_id: str | None = None) -> None:
        if not self.檢查工作階段存取(工作階段識別碼, user_id=user_id):
            raise ValueError(f"session not found: {工作階段識別碼}")
        標題 = 標題.strip()
        if not 標題:
            raise ValueError("title 不可為空")
        with self._交易() as 連線:
            連線.execute("UPDATE sessions SET title=%s,updated_at=%s WHERE id=%s",
                       (標題, self._現在(), 工作階段識別碼))

    def 列出工作階段(self, limit: int = 20, include_children: bool = False,
                 include_archived: bool = False, source: str | None = None,
                 user_id: str | None = None) -> list[dict[str, Any]]:
        條件, 參數 = [], []
        if not include_archived: 條件.append("archived=FALSE")
        if source: 條件.append("source=%s"); 參數.append(source)
        if user_id: 條件.append("user_id=%s"); 參數.append(user_id)
        where = " WHERE " + " AND ".join(條件) if 條件 else ""
        參數.append(max(limit * 5, limit))
        with self._交易() as 連線:
            候選 = self._多列(連線.execute(
                f"SELECT * FROM sessions{where} ORDER BY started_at DESC,id DESC LIMIT %s", tuple(參數)))
        if include_children:
            return 候選[:limit]
        結果, 已見 = [], set()
        for 列 in 候選:
            root = self.取得工作階段譜系(str(列["id"]))[0]
            if root in 已見: continue
            tip = dict(self.讀取工作階段(self.取得壓縮Tip(root)) or 列)
            tip["_lineage_root_id"] = root
            結果.append(tip); 已見.add(root)
            if len(結果) >= limit: break
        return 結果

    def 瀏覽近期工作階段(self, limit: int = 10, include_archived: bool = False,
                    source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        rows = self.列出工作階段(limit, False, include_archived, source, user_id)
        return {"sessions": rows, "total_count": len(rows)}

    @staticmethod
    def _製作snippet(內容: str | None, 詞清單: list[str], 前後: int = 40) -> str:
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
        return ("…" if 片段起 else "") + 文字[片段起:最早位置] + \
            f">>>{文字[最早位置:命中結束]}<<<" + 文字[命中結束:片段迄] + \
            ("…" if 片段迄 < len(文字) else "")

    def 搜尋訊息(self, 查詢: str, limit: int = 20, include_archived: bool = False,
             source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """以 PostgreSQL LIKE 分詞 AND 搜尋，公開錨點使用 ``message_index``。"""
        詞清單 = 查詢.strip().split()
        if not 詞清單:
            return []
        條件 = ["m.active=TRUE"]
        參數: list[Any] = []
        for 詞 in 詞清單:
            條件.append("(LOWER(COALESCE(m.content,'')) LIKE %s OR "
                      "LOWER(COALESCE(m.tool_name,'')) LIKE %s OR "
                      "LOWER(COALESCE(m.tool_calls::text,'')) LIKE %s)")
            模糊 = f"%{詞.lower()}%"
            參數.extend((模糊, 模糊, 模糊))
        if not include_archived:
            條件.append("s.archived=FALSE")
        if source:
            條件.append("s.source=%s")
            參數.append(source)
        if user_id:
            條件.append("s.user_id=%s")
            參數.append(user_id)
        參數.append(int(limit))
        with self._交易() as 連線:
            列清單 = self._多列(連線.execute(
                "SELECT m.session_id,m.message_index,m.role,m.content,m.tool_name "
                "FROM messages m JOIN sessions s ON s.id=m.session_id WHERE " +
                " AND ".join(條件) +
                " ORDER BY m.timestamp DESC NULLS LAST,m.message_index DESC LIMIT %s",
                tuple(參數)))
        return [{"id": int(列["message_index"]), "session_id": 列["session_id"],
                 "role": 列["role"], "content": 列.get("content"),
                 "tool_name": 列.get("tool_name"),
                 "snippet": self._製作snippet(列.get("content"), 詞清單)} for 列 in 列清單]

    def 取得錨點視圖(self, 工作階段識別碼: str, 訊息id: int, window: int = 5,
                bookend: int = 3) -> dict[str, Any]:
        """取得 ``message_index`` 錨點周邊視窗與首尾訊息。"""
        訊息id = int(訊息id)
        with self._交易() as 連線:
            前方 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND message_index<=%s AND active=TRUE "
                "ORDER BY message_index DESC LIMIT %s", (工作階段識別碼, 訊息id, window + 1)))
            後方 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND message_index>%s AND active=TRUE "
                "ORDER BY message_index ASC LIMIT %s", (工作階段識別碼, 訊息id, window)))
            開頭 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND active=TRUE "
                "AND role IN ('user','assistant') AND LENGTH(COALESCE(content,''))>0 "
                "ORDER BY message_index ASC LIMIT %s", (工作階段識別碼, bookend)))
            結尾 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND active=TRUE "
                "AND role IN ('user','assistant') AND LENGTH(COALESCE(content,''))>0 "
                "ORDER BY message_index DESC LIMIT %s", (工作階段識別碼, bookend)))
        轉 = lambda 列: self._資料列轉訊息(列) | {"id": int(列["message_index"])}
        視窗列 = list(reversed(前方)) + 後方
        return {"messages": [轉(列) for 列 in 視窗列],
                "messages_before": max(0, len(前方) - 1),
                "messages_after": len(後方),
                "bookend_start": [轉(列) for 列 in 開頭],
                "bookend_end": [轉(列) for 列 in reversed(結尾)]}

    def 搜尋工作階段(self, 查詢: str, limit: int = 3, window: int = 5,
                include_archived: bool = False, source: str | None = None,
                user_id: str | None = None) -> list[dict[str, Any]]:
        命中清單 = self.搜尋訊息(查詢, max(limit * 5, limit), include_archived, source, user_id)
        結果: list[dict[str, Any]] = []
        已見: set[str] = set()
        for 命中 in 命中清單:
            sid = str(命中["session_id"])
            root = self.取得工作階段譜系(sid)[0]
            if root in 已見:
                continue
            已見.add(root)
            工作階段 = self.讀取工作階段(sid) or {}
            視圖 = self.取得錨點視圖(sid, int(命中["id"]), window=window)
            結果.append({"session_id": sid, "title": 工作階段.get("title"),
                       "source": 工作階段.get("source"), "snippet": 命中.get("snippet"),
                       "match_message_id": 命中["id"], "bookend_start": 視圖["bookend_start"],
                       "messages": 視圖["messages"], "bookend_end": 視圖["bookend_end"],
                       "messages_before": 視圖["messages_before"],
                       "messages_after": 視圖["messages_after"], "_lineage_root_id": root})
            if len(結果) >= limit:
                break
        return 結果

    def 讀取工作階段全文(self, 工作階段識別碼: str,
                   user_id: str | None = None) -> dict[str, Any]:
        工作階段 = self.檢查工作階段存取(工作階段識別碼, user_id=user_id)
        if not 工作階段:
            raise ValueError(f"session not found: {工作階段識別碼}")
        with self._交易() as 連線:
            列清單 = self._多列(連線.execute(
                "SELECT * FROM messages WHERE session_id=%s AND active=TRUE ORDER BY message_index",
                (工作階段識別碼,)))
        總數 = len(列清單)
        保留 = 列清單[:20] + 列清單[-10:] if 總數 > 35 else 列清單
        return {"session_id": 工作階段識別碼, "session": 工作階段,
                "messages": [self._資料列轉訊息(列) | {"id": int(列["message_index"])} for 列 in 保留],
                "total_messages": 總數, "truncated": 總數 > 35}

    def 捲動工作階段訊息(self, 工作階段識別碼: str, around_message_id: int,
                    window: int = 5, user_id: str | None = None) -> dict[str, Any]:
        if not self.檢查工作階段存取(工作階段識別碼, user_id=user_id):
            raise ValueError(f"session not found: {工作階段識別碼}")
        視圖 = self.取得錨點視圖(工作階段識別碼, int(around_message_id), window, 0)
        return {"session_id": 工作階段識別碼, "around_message_id": int(around_message_id),
                "messages": 視圖["messages"], "messages_before": 視圖["messages_before"],
                "messages_after": 視圖["messages_after"]}

    def 匯出工作階段JSONL(self, 輸出路徑: str | Path, limit: int = 1000,
                    include_archived: bool = False, source: str | None = None,
                    user_id: str | None = None) -> dict[str, Any]:
        sessions = self.列出工作階段(limit=limit, include_archived=include_archived,
                              source=source, user_id=user_id)
        路徑 = Path(輸出路徑).expanduser()
        路徑.parent.mkdir(parents=True, exist_ok=True)
        訊息總數 = 0
        with 路徑.open("w", encoding="utf-8") as handle:
            for session in sessions:
                messages = self.讀取訊息(str(session["id"]), include_ancestors=True, user_id=user_id)
                訊息總數 += len(messages)
                handle.write(json.dumps({"session": session, "messages": messages},
                                        ensure_ascii=False, default=str) + "\n")
        return {"output": str(路徑), "session_count": len(sessions),
                "message_count": 訊息總數}

    def 統計工作階段(self, include_archived: bool = False, source: str | None = None,
                 user_id: str | None = None) -> dict[str, Any]:
        條件, 參數 = [], []
        if not include_archived: 條件.append("archived=FALSE")
        if source: 條件.append("source=%s"); 參數.append(source)
        if user_id: 條件.append("user_id=%s"); 參數.append(user_id)
        where = " WHERE " + " AND ".join(條件) if 條件 else ""
        with self._交易() as 連線:
            sessions = self._一列(連線.execute(
                f"SELECT COUNT(*) AS session_count FROM sessions{where}", tuple(參數))) or {}
            usage = self._一列(連線.execute(
                "SELECT COUNT(*) AS api_call_count,COALESCE(SUM(input_tokens),0) AS input_tokens,"
                "COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens,"
                "COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd FROM session_usage_events" +
                (" WHERE user_id=%s" if user_id else ""), ((user_id,) if user_id else ()))) or {}
        return {"session_count": int(sessions.get("session_count") or 0),
                "api_call_count": int(usage.get("api_call_count") or 0),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
                "estimated_cost_usd": float(usage.get("estimated_cost_usd") or 0),
                "backend": "postgres"}


setattr(PostgreSQL工作階段庫, "append_message", PostgreSQL工作階段庫.附加單一訊息)
setattr(PostgreSQL工作階段庫, "replace_messages", PostgreSQL工作階段庫.替換訊息清單)
