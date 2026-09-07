"""PostgreSQL owner-safe 與 exact-admin invocation 投影。"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from 繁中代理.PostgreSQL連線 import 交易連線
from ..領域模型 import InvocationRef, PublishedUsage
from .管理查詢契約 import (
    查詢投影錯誤, 管理員呼叫不存在錯誤, 管理員呼叫查詢錯誤,
    建立擁有者安全詳情, 建立管理員呼叫完整詳情,
    管理員呼叫查詢條件, 管理員呼叫游標位置,
    管理員呼叫列表項目, 管理員呼叫投影頁,
)

_擁有者欄 = ("id", "request_id", "session_id", "endpoint_version_id", "status", "error_code", "latency_ms", "usage")
_呼叫欄 = ("id", "endpoint_id", "endpoint_version_id", "credential_id", "request_id", "session_id", "message_id",
         "status", "input", "metadata", "output", "error", "usage", "metadata_size_bytes", "metadata_sha256",
         "latency_ms", "pricing_version", "created_at_epoch", "completed_at_epoch")
_事件欄 = ("id", "sequence_number", "event_type", "payload", "created_at_epoch")
_工具欄 = ("id", "run_event_id", "sequence_number", "tool_name", "arguments", "outcome", "result", "error",
         "latency_ms", "retry_of_tool_call_id", "created_at_epoch")
_遮蔽欄 = ("id", "target_type", "target_row_id", "json_path", "original_sha256", "reason", "actor_type", "actor_id",
         "audit_event_id", "is_tombstone", "redacted_at_epoch")
_命中欄 = ("id", "tool_call_id", "target_type", "detector_type", "json_path", "start_offset", "end_offset",
         "audit_event_id", "detected_at_epoch")
_列表欄 = ("id", "endpoint_id", "endpoint_version_id", "request_id", "status", "error_code", "latency_ms",
         "created_at_epoch", "completed_at_epoch", "has_redactions")


def _id(v: object) -> bool:
    return type(v) is str and 0 < len(v) <= 256 and not any(c.isspace() for c in v)


def _json(v: object) -> Any:
    """接受 psycopg JSONB 的已解碼值，也接受測試/舊 driver 的 JSON 字串。"""
    if v is None: return None
    return json.loads(v) if type(v) is str else json.loads(json.dumps(v, ensure_ascii=False, allow_nan=False))


def _obj(v: object) -> dict[str, Any]:
    if v is None: return {}
    x = _json(v)
    if type(x) is not dict: raise ValueError
    return x


def _正規列(列: object, 欄名: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(列, Mapping):
        if set(列) != set(欄名): raise ValueError
        return tuple(列[名稱] for 名稱 in 欄名)
    if type(列) is tuple and len(列) == len(欄名): return 列
    raise ValueError


def _正規多列(列們: Sequence[object], 欄名: tuple[str, ...]) -> tuple[tuple[Any, ...], ...]:
    return tuple(_正規列(列, 欄名) for 列 in 列們)


class PostgreSQL呼叫查詢投影:
    __slots__ = ("_設定",)

    def __init__(self, 凍結設定: object) -> None:
        self._設定 = 凍結設定

    def 查詢擁有者診斷(self, 擁有者識別碼: str, 端點識別碼: str, 呼叫識別碼: str, /) -> dict[str, Any]:
        """單一 owner composite gate；SQL 不選 input/output/metadata/tool arguments。"""
        try:
            if not all(_id(v) for v in (擁有者識別碼, 端點識別碼, 呼叫識別碼)): raise ValueError
            with 交易連線(self._設定) as c:
                raw = c.execute(
                    "SELECT i.id,i.request_id,i.session_id,i.endpoint_version_id,i.status,se.error_code,"
                    "i.latency_ms,i.usage FROM endpoint_invocations i JOIN published_endpoints e "
                    "ON e.id=i.endpoint_id LEFT JOIN endpoint_invocation_safe_errors se ON se.invocation_id=i.id "
                    "WHERE e.owner_user_id=%s AND e.id=%s AND i.id=%s",
                    (擁有者識別碼, 端點識別碼, 呼叫識別碼)).fetchone()
                if raw is None: raise ValueError
                row = _正規列(raw, _擁有者欄)
                tools = _正規多列(c.execute(
                    "SELECT tool_name FROM endpoint_tool_calls WHERE invocation_id=%s ORDER BY sequence_number",
                    (呼叫識別碼,)).fetchall(), ("tool_name",))
            usage = _obj(row[7]); total = usage.get("total_tokens")
            if total is not None and (type(total) is not int or total < 0): raise ValueError
            return {"invocation": InvocationRef(row[0], row[1], row[2]).to_json(),
                    "endpoint_version_id": row[3], "status": row[4], "error_code": row[5],
                    "schema_path": None, "latency_ms": row[6], "usage": PublishedUsage(total).to_json(),
                    "tool_names": [r[0] for r in tools]}
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 查詢投影錯誤("呼叫紀錄不可取得") from None

    def 查詢擁有者安全詳情(self, *args) -> Any:
        return 建立擁有者安全詳情(self.查詢擁有者診斷(*args))

    def 管理員呼叫配對存在(self, 端點識別碼: str, 呼叫識別碼: str, /) -> bool:
        try:
            if not _id(端點識別碼) or not _id(呼叫識別碼): raise ValueError
            with 交易連線(self._設定) as c:
                rows = c.execute(
                    "SELECT 1 AS exists_marker FROM endpoint_invocations WHERE endpoint_id=%s AND id=%s LIMIT 2",
                    (端點識別碼, 呼叫識別碼)).fetchall()
            if len(rows) > 1: raise ValueError
            if rows: _正規列(rows[0], ("exists_marker",))
            return len(rows) == 1
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None

    def 查詢管理員原始資料(self, 管理員授權: bool, 端點識別碼: str, 呼叫識別碼: str, /) -> dict[str, Any]:
        try:
            if type(管理員授權) is not bool or not 管理員授權 or not _id(端點識別碼) or not _id(呼叫識別碼):
                raise ValueError
            with 交易連線(self._設定) as c:
                raw = c.execute(
                    "SELECT id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,message_id,status,"
                    "input,metadata,output,error,usage,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,"
                    "EXTRACT(EPOCH FROM created_at)::double precision AS created_at_epoch,"
                    "EXTRACT(EPOCH FROM completed_at)::double precision AS completed_at_epoch "
                    "FROM endpoint_invocations WHERE endpoint_id=%s AND id=%s",
                    (端點識別碼, 呼叫識別碼)).fetchone()
                if raw is None: raise 管理員呼叫不存在錯誤("找不到呼叫紀錄")
                row = _正規列(raw, _呼叫欄)
                events = _正規多列(c.execute(
                    "SELECT id,sequence_number,event_type,payload,EXTRACT(EPOCH FROM created_at)::double precision AS created_at_epoch "
                    "FROM run_events WHERE invocation_id=%s ORDER BY sequence_number", (呼叫識別碼,)).fetchall(), _事件欄)
                tools = _正規多列(c.execute(
                    "SELECT id,run_event_id,sequence_number,tool_name,arguments,outcome,result,error,latency_ms,"
                    "retry_of_tool_call_id,EXTRACT(EPOCH FROM created_at)::double precision AS created_at_epoch "
                    "FROM endpoint_tool_calls WHERE invocation_id=%s ORDER BY sequence_number", (呼叫識別碼,)).fetchall(), _工具欄)
                reds = _正規多列(c.execute(
                    "SELECT id,target_type,target_row_id,json_path,original_sha256,reason,actor_type,actor_id,audit_event_id,"
                    "is_tombstone,EXTRACT(EPOCH FROM redacted_at)::double precision AS redacted_at_epoch "
                    "FROM endpoint_redactions WHERE invocation_id=%s ORDER BY redacted_at,id", (呼叫識別碼,)).fetchall(), _遮蔽欄)
                hits = _正規多列(c.execute(
                    "SELECT id,tool_call_id,target_type,detector_type,json_path,start_offset,end_offset,audit_event_id,"
                    "EXTRACT(EPOCH FROM detected_at)::double precision AS detected_at_epoch FROM invocation_sensitive_hits "
                    "WHERE invocation_id=%s ORDER BY target_type,tool_call_id,json_path,start_offset,end_offset,detector_type,id",
                    (呼叫識別碼,)).fetchall(), _命中欄)
            return _admin(row, events, tools, reds, hits)
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except 管理員呼叫不存在錯誤: raise
        except BaseException: raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None

    def 查詢管理員完整詳情(self, *args) -> Any:
        return 建立管理員呼叫完整詳情(self.查詢管理員原始資料(*args))

    def 列出管理員安全呼叫(self, 條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置 | None, /) -> 管理員呼叫投影頁:
        try:
            if type(條件) is not 管理員呼叫查詢條件 or (位置 is not None and type(位置) is not 管理員呼叫游標位置):
                raise ValueError
            p = (條件.端點識別碼, 條件.起始時間, 條件.起始時間, 條件.結束時間, 條件.結束時間,
                 條件.狀態, 條件.狀態, 條件.錯誤碼, 條件.錯誤碼, None if 位置 is None else 位置.建立時間,
                 None if 位置 is None else 位置.建立時間, None if 位置 is None else 位置.建立時間,
                 None if 位置 is None else 位置.呼叫識別碼, 條件.數量上限 + 1)
            with 交易連線(self._設定) as c:
                rows = _正規多列(c.execute(
                    "SELECT i.id,i.endpoint_id,i.endpoint_version_id,i.request_id,i.status,se.error_code,i.latency_ms,"
                    "EXTRACT(EPOCH FROM i.created_at)::double precision AS created_at_epoch,"
                    "EXTRACT(EPOCH FROM i.completed_at)::double precision AS completed_at_epoch,"
                    "EXISTS(SELECT 1 FROM endpoint_redactions r WHERE r.invocation_id=i.id) AS has_redactions "
                    "FROM endpoint_invocations i LEFT JOIN endpoint_invocation_safe_errors se ON se.invocation_id=i.id "
                    "WHERE i.endpoint_id=%s AND (%s::double precision IS NULL OR i.created_at>=to_timestamp(%s)) "
                    "AND (%s::double precision IS NULL OR i.created_at<=to_timestamp(%s)) AND (%s::text IS NULL OR i.status=%s) "
                    "AND (%s::text IS NULL OR se.error_code=%s) AND (%s::double precision IS NULL OR i.created_at<to_timestamp(%s) "
                    "OR (i.created_at=to_timestamp(%s) AND i.id<%s)) ORDER BY i.created_at DESC,i.id DESC LIMIT %s",
                    p).fetchall(), _列表欄)
            items = tuple(管理員呼叫列表項目(*r) for r in rows[:條件.數量上限])
            nxt = 管理員呼叫游標位置(items[-1].建立時間, items[-1].呼叫識別碼) if len(rows) > 條件.數量上限 else None
            return 管理員呼叫投影頁(items, nxt)
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None


def _admin(r, events, tools, reds, hits):
    return {"invocation": InvocationRef(r[0], r[4], r[5]).to_json(), "endpoint_id": r[1],
            "endpoint_version_id": r[2], "credential_id": r[3], "message_id": r[6], "status": r[7],
            "input": _json(r[8]), "metadata": _json(r[9]), "output": _json(r[10]), "error": _json(r[11]),
            "usage": _json(r[12]), "metadata_size_bytes": r[13], "metadata_sha256": r[14],
            "latency_ms": r[15], "pricing_version": r[16], "created_at": r[17], "completed_at": r[18],
            "run_events": [{"id": x[0], "sequence_number": x[1], "event_type": x[2], "payload": _json(x[3]),
                            "created_at": x[4]} for x in events],
            "tool_calls": [{"id": x[0], "run_event_id": x[1], "sequence_number": x[2], "tool_name": x[3],
                            "arguments": _json(x[4]), "outcome": x[5], "result": _json(x[6]), "error": _json(x[7]),
                            "latency_ms": x[8], "retry_of_tool_call_id": x[9], "created_at": x[10]} for x in tools],
            "redactions": [{"id": x[0], "target_type": x[1], "target_row_id": x[2], "json_path": x[3],
                            "original_sha256": x[4], "reason": x[5], "actor": {"type": x[6], "id": x[7]},
                            "audit_event_id": x[8], "is_tombstone": bool(x[9]), "redacted_at": x[10]} for x in reds],
            "sensitive_hits": [{"id": x[0], "tool_call_id": x[1], "target": x[2], "detector_type": x[3],
                                "json_path": x[4], "start": x[5], "end": x[6], "detected_at": x[8]} for x in hits]}
