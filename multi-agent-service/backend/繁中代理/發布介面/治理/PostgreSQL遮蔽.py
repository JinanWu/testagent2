"""PostgreSQL 不可逆 JSON 遮蔽與 tombstone 寫入。"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Any

from 繁中代理.PostgreSQL連線 import 交易連線
from .遮蔽 import (不可逆遮蔽錯誤, 遮蔽目標衝突, 遮蔽路徑無效,
                  _解析JSON, _尋找JSON位置, _建立正規JSON, 驗證遮蔽公開欄位)

_目標 = {
    "invocation_input": ("endpoint_invocations", "input"),
    "metadata": ("endpoint_invocations", "metadata"),
    "output": ("endpoint_invocations", "output"),
    "error": ("endpoint_invocations", "error"),
    "run_event": ("run_events", "payload"),
    "tool_arguments": ("endpoint_tool_calls", "arguments"),
    "tool_result": ("endpoint_tool_calls", "result"),
    "tool_error": ("endpoint_tool_calls", "error"),
}


def _安全識別碼(值: object) -> bool:
    return type(值) is str and 0 < len(值) <= 256 and 值.strip() == 值 and not any(c.isspace() for c in 值)


class PostgreSQL不可逆遮蔽服務:
    """以單一 PostgreSQL 交易提交 canonical audit、墓碑與 append-only ledger。"""

    __slots__ = ("_設定",)

    def __init__(self, 凍結設定: object) -> None:
        self._設定 = 凍結設定

    def 遮蔽(
        self, 管理員授權: bool, 遮蔽識別碼: str, 稽核事件識別碼: str,
        操作者識別碼: str, 請求識別碼: str, 呼叫識別碼: str,
        目標類型: str, 目標列識別碼: str, JSON路徑: str, 原因: str,
        發生時間: int | float, /,
    ) -> dict[str, Any]:
        """在同一交易冪等提交 JSON mutation 與完整 durable redaction graph。"""
        try:
            驗證遮蔽公開欄位(目標類型, JSON路徑, 原因)
            if (管理員授權 is not True
                    or not all(_安全識別碼(v) for v in (
                        遮蔽識別碼, 稽核事件識別碼, 操作者識別碼, 請求識別碼,
                        呼叫識別碼, 目標列識別碼,
                    ))
                    or type(發生時間) not in (int, float)
                    or not math.isfinite(發生時間) or 發生時間 < 0):
                raise ValueError
            表格, 欄位 = _目標[目標類型]
            時間 = float(發生時間)
            with 交易連線(self._設定) as 連線:
                endpoint列 = 連線.execute(
                    "SELECT endpoint_id FROM endpoint_invocations WHERE id=%s",
                    (呼叫識別碼,),
                ).fetchone()
                if endpoint列 is not None: endpoint列 = _正規列(endpoint列, ("endpoint_id",))
                if endpoint列 is None or not _安全識別碼(endpoint列[0]):
                    raise ValueError
                endpoint_id = endpoint列[0]
                指紋內容 = _建立正規JSON({
                    "endpoint_id": endpoint_id, "invocation_id": 呼叫識別碼,
                    "target_type": 目標類型, "target_row_id": 目標列識別碼,
                    "json_path": JSON路徑, "reason": 原因,
                })
                指紋 = hashlib.sha256(指紋內容.encode("utf-8")).hexdigest()
                連線.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"redaction:{操作者識別碼}:{請求識別碼}",),
                )
                已有 = 連線.execute(
                    "SELECT c.request_fingerprint,r.id,r.target_type,r.target_row_id,r.json_path,"
                    "r.original_sha256,r.reason,r.actor_id,r.audit_event_id,r.redacted_at "
                    "FROM redaction_idempotency_commands c JOIN endpoint_redactions r "
                    "ON r.id=c.redaction_id WHERE c.principal_id=%s AND c.idempotency_key=%s",
                    (操作者識別碼, 請求識別碼),
                ).fetchone()
                if 已有 is not None:
                    已有 = _正規列(已有, ("request_fingerprint","id","target_type","target_row_id",
                        "json_path","original_sha256","reason","actor_id","audit_event_id","redacted_at"))
                    if 已有[0] != 指紋:
                        raise ValueError
                    return {
                        "redaction_id": 已有[1], "target_type": 已有[2],
                        "target_row_id": 已有[3], "json_path": 已有[4],
                        "original_sha256": 已有[5], "reason": 已有[6],
                        "actor_id": 已有[7], "audit_event_id": 已有[8],
                        "is_tombstone": True,
                        "redacted_at": float(已有[9].timestamp() if hasattr(已有[9], "timestamp") else 已有[9]),
                    }
                if 表格 == "endpoint_invocations":
                    列 = 連線.execute(
                        f"SELECT id,{欄位} FROM {表格} WHERE id=%s FOR UPDATE",
                        (目標列識別碼,),
                    ).fetchone()
                else:
                    列 = 連線.execute(
                        f"SELECT invocation_id,{欄位} FROM {表格} WHERE id=%s FOR UPDATE",
                        (目標列識別碼,),
                    ).fetchone()
                if 列 is not None:
                    列 = _正規列(列, (("id" if 表格 == "endpoint_invocations" else "invocation_id"), 欄位))
                if 列 is None or 列[0] != 呼叫識別碼 or 列[1] is None:
                    raise ValueError
                payload = _解析JSON(列[1] if type(列[1]) is str else _建立正規JSON(列[1]))
                容器, 鍵 = _尋找JSON位置(payload, JSON路徑)
                原值 = payload if JSON路徑 == "" else 容器[鍵]
                摘要 = hashlib.sha256(_建立正規JSON(原值).encode("utf-8")).hexdigest()
                tombstone = {"$tombstone": {
                    "redaction_id": 遮蔽識別碼, "redacted_at": 時間,
                }}
                if JSON路徑 == "": payload = tombstone
                else: 容器[鍵] = tombstone
                寫入 = (
                    (f"UPDATE {表格} SET {欄位}=%s::jsonb WHERE id=%s",
                     (_建立正規JSON(payload), 目標列識別碼)),
                    ("INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
                     "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata,created_at) "
                     "VALUES (%s,%s,to_timestamp(%s),'audit.payload.redact','success','admin',%s,"
                     "'endpoint.redaction',%s,%s,%s,%s,%s::jsonb,to_timestamp(%s))",
                     (稽核事件識別碼, 稽核事件識別碼, 時間, 操作者識別碼, 遮蔽識別碼,
                      請求識別碼, endpoint_id, 呼叫識別碼, '{"is_tombstone":true}', 時間)),
                    ("INSERT INTO endpoint_redactions(id,invocation_id,target_type,target_row_id,json_path,"
                     "original_sha256,reason,actor_type,actor_id,audit_event_id,is_tombstone,redacted_at) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,'admin',%s,%s,TRUE,to_timestamp(%s))",
                     (遮蔽識別碼, 呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑,
                      摘要, 原因, 操作者識別碼, 稽核事件識別碼, 時間)),
                )
                for SQL, 參數 in 寫入:
                    if getattr(連線.execute(SQL, 參數), "rowcount", None) != 1:
                        raise ValueError
                目標身分 = hashlib.sha256(_建立正規JSON([
                    endpoint_id, 呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑,
                ]).encode("utf-8")).hexdigest()
                if getattr(連線.execute(
                    "INSERT INTO redaction_tombstones(redaction_id,invocation_id,target_identity_sha256,retained_until,created_at) "
                    "VALUES (%s,%s,%s,to_timestamp(%s)+INTERVAL '5 years',to_timestamp(%s))",
                    (遮蔽識別碼, 呼叫識別碼, 目標身分, 時間, 時間),
                ), "rowcount", None) != 1:
                    raise ValueError
                if getattr(連線.execute(
                    "INSERT INTO redaction_idempotency_commands(principal_id,idempotency_key,request_fingerprint,"
                    "redaction_id,audit_event_id,request_id,endpoint_id,invocation_id,target_type,target_row_id,"
                    "json_path,reason,first_seen_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s))",
                    (操作者識別碼, 請求識別碼, 指紋, 遮蔽識別碼, 稽核事件識別碼,
                     請求識別碼, endpoint_id, 呼叫識別碼, 目標類型, 目標列識別碼,
                     JSON路徑, 原因, 時間),
                ), "rowcount", None) != 1:
                    raise ValueError
                return {
                    "redaction_id": 遮蔽識別碼, "target_type": 目標類型,
                    "target_row_id": 目標列識別碼, "json_path": JSON路徑,
                    "original_sha256": 摘要, "reason": 原因,
                    "actor_id": 操作者識別碼, "audit_event_id": 稽核事件識別碼,
                    "is_tombstone": True, "redacted_at": 時間,
                }
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except (遮蔽路徑無效, 遮蔽目標衝突): raise
        except BaseException: raise 不可逆遮蔽錯誤("不可逆遮蔽失敗") from None

    redact = 遮蔽


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError
