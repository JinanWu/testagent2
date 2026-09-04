"""PostgreSQL 呼叫、執行事件與工具紀錄儲存庫。"""
from __future__ import annotations

import math
import secrets
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from 繁中代理.PostgreSQL連線 import 交易連線
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from .儲存庫 import 呼叫儲存錯誤

_終態 = frozenset({"succeeded", "failed", "rate_limited", "invalid_api_key"})


def _識別碼(值: object) -> str:
    if type(值) is not str or not 值.strip() or len(值) > 256:
        raise ValueError
    return 值


def _時間(值: object) -> float:
    if type(值) not in (int, float) or not math.isfinite(值) or 值 < 0:
        raise ValueError
    return float(值)


def _JSON(值: object) -> str:
    return 建立正規JSON(值)


def _資料庫JSON(值: object) -> str | None:
    if 值 is None:
        return None
    return _JSON(解析嚴格JSON(值) if type(值) is str else 值)


def _正規列(值: object, 欄名: tuple[str, ...]) -> tuple[Any, ...]:
    if isinstance(值, Mapping):
        if set(值) != set(欄名):
            raise ValueError
        return tuple(值[名稱] for 名稱 in 欄名)
    if type(值) is tuple and len(值) == len(欄名):
        return 值
    raise ValueError


def _列(游標: object, 欄名: tuple[str, ...]) -> tuple[Any, ...] | None:
    值 = 游標.fetchone()
    if 值 is None:
        return None
    return _正規列(值, 欄名)


class PostgreSQL呼叫儲存庫:
    """透過 ``交易連線`` 使用現行 invocation/run/tool 資料表。"""

    __slots__ = ("_設定", "_時鐘", "_識別碼工廠")

    def __init__(self, 凍結設定: object, *, 時鐘: Callable[[], float] = time.time,
                 識別碼工廠: Callable[[], str] | None = None) -> None:
        if not callable(時鐘) or (識別碼工廠 is not None and not callable(識別碼工廠)):
            raise 呼叫儲存錯誤("呼叫儲存庫初始化失敗") from None
        self._設定 = 凍結設定
        self._時鐘 = 時鐘
        self._識別碼工廠 = 識別碼工廠 or (lambda: f"inv-{secrets.token_hex(16)}")

    def 建立已解析呼叫(
        self, endpoint_id: str, endpoint_version_id: str, request_id: str, input: object, *,
        credential_id: str | None = None, session_id: str | None = None,
        message_id: str | None = None, metadata: object | None = None,
        metadata_size_bytes: int | None = None, metadata_sha256: str | None = None,
    ) -> str:
        """以 request_id 冪等建立 pending invocation；衝突內容一律失敗關閉。"""
        try:
            for 值 in (endpoint_id, endpoint_version_id, request_id):
                _識別碼(值)
            for 值 in (credential_id, session_id, message_id):
                if 值 is not None:
                    _識別碼(值)
            if metadata_size_bytes is not None and (type(metadata_size_bytes) is not int or metadata_size_bytes < 0):
                raise ValueError
            if metadata_sha256 is not None and (type(metadata_sha256) is not str or len(metadata_sha256) != 64):
                raise ValueError
            輸入JSON = _JSON(input)
            中繼JSON = None if metadata is None else _JSON(metadata)
            with 交易連線(self._設定) as 連線:
                呼叫ID = _識別碼(self._識別碼工廠())
                建立時間 = _時間(self._時鐘())
                游標 = 連線.execute(
                    "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,credential_id,"
                    "request_id,session_id,message_id,status,input,metadata,metadata_size_bytes,"
                    "metadata_sha256,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',%s::jsonb,%s::jsonb,%s,%s,to_timestamp(%s)) "
                    "ON CONFLICT(request_id) DO NOTHING RETURNING id",
                    (呼叫ID, endpoint_id, endpoint_version_id, credential_id, request_id, session_id,
                     message_id, 輸入JSON, 中繼JSON, metadata_size_bytes, metadata_sha256, 建立時間))
                勝者 = _列(游標, ("id",))
                if 勝者 is not None:
                    if len(勝者) != 1 or 勝者[0] != 呼叫ID:
                        raise ValueError
                    return 呼叫ID
                已有欄名 = ("id", "endpoint_id", "endpoint_version_id", "credential_id", "session_id",
                         "message_id", "status", "input", "metadata", "metadata_size_bytes", "metadata_sha256")
                已有 = _列(連線.execute(
                    "SELECT id,endpoint_id,endpoint_version_id,credential_id,session_id,message_id,status,"
                    "input,metadata,metadata_size_bytes,metadata_sha256 FROM endpoint_invocations "
                    "WHERE request_id=%s", (request_id,)), 已有欄名)
                if (已有 is None or len(已有) != 11 or
                        (已有[1:7] + (_資料庫JSON(已有[7]), _資料庫JSON(已有[8])) + 已有[9:]) !=
                        (endpoint_id, endpoint_version_id, credential_id, session_id, message_id,
                         "pending", 輸入JSON, 中繼JSON, metadata_size_bytes, metadata_sha256)):
                    raise ValueError
                return _識別碼(已有[0])
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 呼叫儲存錯誤("呼叫建立失敗") from None

    def 標記執行中(self, invocation_id: str) -> None:
        self._轉換狀態(invocation_id, "pending", "running")

    def 完成呼叫(self, invocation_id: str, status: str, *, output: object | None = None,
             error: object | None = None, usage: object | None = None,
             latency_ms: float | None = None) -> None:
        """依 canonical 狀態矩陣將 pending／running invocation 明確結案。"""
        try:
            _識別碼(invocation_id)
            允許來源 = {
                "succeeded": ("running",),
                "failed": ("pending", "running"),
                "rate_limited": ("pending",),
                "invalid_api_key": ("pending",),
            }[status]
            是否成功 = status == "succeeded"
            if ((是否成功 and (output is None or error is not None))
                    or (not 是否成功 and (output is not None or error is None))
                    or latency_ms is not None and (type(latency_ms) not in (int, float)
                                                   or not math.isfinite(float(latency_ms))
                                                   or latency_ms < 0)):
                raise ValueError
            with 交易連線(self._設定) as 連線:
                游標 = 連線.execute(
                    "UPDATE endpoint_invocations SET status=%s,output=%s::jsonb,error=%s::jsonb,"
                    "usage=%s::jsonb,latency_ms=%s,completed_at=to_timestamp(%s) "
                    "WHERE id=%s AND status=ANY(%s)",
                    (status, None if output is None else _JSON(output),
                     None if error is None else _JSON(error),
                     None if usage is None else _JSON(usage), latency_ms,
                     _時間(self._時鐘()), invocation_id, list(允許來源)),
                )
                if getattr(游標, "rowcount", None) != 1:
                    raise ValueError
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 呼叫儲存錯誤("呼叫結案失敗") from None

    def _轉換狀態(self, invocation_id: str, 原狀態: str, 新狀態: str) -> None:
        try:
            _識別碼(invocation_id)
            with 交易連線(self._設定) as 連線:
                游標 = 連線.execute(
                    "UPDATE endpoint_invocations SET status=%s WHERE id=%s AND status=%s",
                    (新狀態, invocation_id, 原狀態))
                if getattr(游標, "rowcount", None) != 1:
                    raise ValueError
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 呼叫儲存錯誤("呼叫狀態更新失敗") from None

    def 附加執行事件(self, invocation_id: str, event_id: str, event_type: str,
               payload: object) -> int:
        """鎖定 invocation 後配置單調序號並寫入 run_events。"""
        try:
            for 值 in (invocation_id, event_id, event_type):
                _識別碼(值)
            payload_json = _JSON(payload)
            with 交易連線(self._設定) as 連線:
                狀態列 = _列(連線.execute(
                    "SELECT status FROM endpoint_invocations WHERE id=%s FOR UPDATE", (invocation_id,)), ("status",))
                if 狀態列 != ("running",):
                    raise ValueError
                序號列 = _列(連線.execute(
                    "SELECT COALESCE(MAX(sequence_number),0) AS max_sequence_number FROM run_events WHERE invocation_id=%s",
                    (invocation_id,)), ("max_sequence_number",))
                if 序號列 is None or len(序號列) != 1 or type(序號列[0]) is not int:
                    raise ValueError
                序號 = 序號列[0] + 1
                游標 = 連線.execute(
                    "INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload,created_at) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb,to_timestamp(%s))",
                    (event_id, invocation_id, 序號, event_type, payload_json, _時間(self._時鐘())))
                if getattr(游標, "rowcount", None) != 1:
                    raise ValueError
                return 序號
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 呼叫儲存錯誤("執行事件附加失敗") from None

    def 附加工具呼叫(
        self, invocation_id: str, tool_call_id: str, tool_name: str, arguments: object, *,
        outcome: str, result: object | None = None, error: object | None = None,
        run_event_id: str | None = None, retry_of_tool_call_id: str | None = None,
        latency_ms: int | float | None = None,
    ) -> int:
        """鎖定 invocation 後配置序號並原子附加 endpoint_tool_calls。"""
        try:
            for 值 in (invocation_id, tool_call_id, tool_name):
                _識別碼(值)
            if run_event_id is not None: _識別碼(run_event_id)
            if retry_of_tool_call_id is not None: _識別碼(retry_of_tool_call_id)
            if outcome not in ("success", "error") or ((outcome == "success") != (result is not None and error is None)):
                raise ValueError
            if latency_ms is not None:
                latency_ms = _時間(latency_ms)
            參數JSON = _JSON(arguments)
            結果JSON = None if result is None else _JSON(result)
            錯誤JSON = None if error is None else _JSON(error)
            with 交易連線(self._設定) as 連線:
                if _列(連線.execute("SELECT status FROM endpoint_invocations WHERE id=%s FOR UPDATE",
                                    (invocation_id,)), ("status",)) != ("running",):
                    raise ValueError
                序號列 = _列(連線.execute(
                    "SELECT COALESCE(MAX(sequence_number),0) AS max_sequence_number FROM endpoint_tool_calls WHERE invocation_id=%s",
                    (invocation_id,)), ("max_sequence_number",))
                if 序號列 is None or type(序號列[0]) is not int: raise ValueError
                序號 = 序號列[0] + 1
                游標 = 連線.execute(
                    "INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,"
                    "arguments,outcome,result,error,latency_ms,retry_of_tool_call_id,created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,to_timestamp(%s))",
                    (tool_call_id, invocation_id, run_event_id, 序號, tool_name, 參數JSON, outcome,
                     結果JSON, 錯誤JSON, latency_ms, retry_of_tool_call_id, _時間(self._時鐘())))
                if getattr(游標, "rowcount", None) != 1: raise ValueError
                return 序號
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 呼叫儲存錯誤("工具呼叫附加失敗") from None

    def 結案(self, invocation_id: str, status: str, *, output: object | None = None,
             error: object | None = None, usage: object | None = None,
             latency_ms: int | float | None = None, pricing_version: str | None = None) -> None:
        """只允許 running 到明確終態，並在同一交易保存 terminal payload。"""
        try:
            _識別碼(invocation_id)
            if status not in _終態: raise ValueError
            if status == "succeeded" and (output is None or error is not None): raise ValueError
            if status == "failed" and (error is None or output is not None): raise ValueError
            if latency_ms is not None: latency_ms = _時間(latency_ms)
            if pricing_version is not None: _識別碼(pricing_version)
            with 交易連線(self._設定) as 連線:
                游標 = 連線.execute(
                    "UPDATE endpoint_invocations SET status=%s,output=%s::jsonb,error=%s::jsonb,usage=%s::jsonb,"
                    "latency_ms=%s,pricing_version=%s,completed_at=to_timestamp(%s) WHERE id=%s AND status='running'",
                    (status, None if output is None else _JSON(output), None if error is None else _JSON(error),
                     None if usage is None else _JSON(usage), latency_ms, pricing_version,
                     _時間(self._時鐘()), invocation_id))
                if getattr(游標, "rowcount", None) != 1: raise ValueError
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 呼叫儲存錯誤("呼叫結案失敗") from None

    def 原子記錄執行事件並結案(
        self, invocation_id: str, event_id: str, event_type: str, payload: object,
        expected_sequence: int, *, status: str | None = None, output: object | None = None,
        error: object | None = None, usage: object | None = None,
        工作階段對話組: object | None = None, warnings: object = None,
    ) -> int | tuple[int, tuple[tuple[str, str], ...]]:
        """Atomically append the terminal event and transition running to terminal."""
        try:
            if any(type(x) is not str or not x.strip() for x in (invocation_id, event_id, event_type)) or type(expected_sequence) is not int or expected_sequence < 1:
                raise ValueError
            if status not in (None, "succeeded", "failed"):
                raise ValueError
            if status is None and any(value is not None for value in (output, error, usage, 工作階段對話組)):
                raise ValueError
            if status is not None and ((status == "succeeded") != (output is not None and error is None)):
                raise ValueError
            warning_values = tuple((str(getattr(x, "code")), str(getattr(x, "message"))) for x in (warnings or ()))
            with 交易連線(self._設定) as 連線:
                row = _正規列(連線.execute("SELECT status FROM endpoint_invocations WHERE id=%s FOR UPDATE", (invocation_id,)).fetchone(), ("status",))
                if row != ("running",):
                    raise ValueError
                current = _正規列(連線.execute("SELECT COALESCE(MAX(sequence_number),0) AS n FROM run_events WHERE invocation_id=%s", (invocation_id,)).fetchone(), ("n",))
                if current is None or current[0] + 1 != expected_sequence:
                    raise ValueError
                now = _時間(self._時鐘())
                連線.execute("INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload,created_at) VALUES(%s,%s,%s,%s,%s::jsonb,to_timestamp(%s))", (event_id, invocation_id, expected_sequence, event_type, _JSON(payload), now))
                if status is not None:
                    changed = 連線.execute("UPDATE endpoint_invocations SET status=%s,output=%s::jsonb,error=%s::jsonb,usage=%s::jsonb,completed_at=to_timestamp(%s) WHERE id=%s AND status='running'", (status, None if output is None else _JSON(output), None if error is None else _JSON(error), None if usage is None else _JSON(usage), now, invocation_id))
                    if getattr(changed, "rowcount", 0) != 1:
                        raise ValueError
            return (expected_sequence, warning_values) if status == "succeeded" else expected_sequence
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 呼叫儲存錯誤("原子呼叫結案失敗") from None

    def 原子寫入呼叫圖形(
        self, 呼叫: Mapping[str, object], *, 執行事件: Iterable[Mapping[str, object]] = (),
        工具呼叫: Iterable[Mapping[str, object]] = (), 稽核事件: Iterable[Mapping[str, object]] = (),
        敏感命中: Iterable[Mapping[str, object]] = (),
    ) -> None:
        """以一個 ``交易連線`` 原子寫入 invocation/run/tool/audit/sensitive 圖形。"""
        try:
            with 交易連線(self._設定) as 連線:
                _插入映射(連線, "endpoint_invocations", 呼叫)
                for 項 in 執行事件: _插入映射(連線, "run_events", 項)
                for 項 in 工具呼叫: _插入映射(連線, "endpoint_tool_calls", 項)
                for 項 in 稽核事件: _插入映射(連線, "audit_events", 項)
                for 項 in 敏感命中: _插入映射(連線, "invocation_sensitive_hits", 項)
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 呼叫儲存錯誤("呼叫圖形寫入失敗") from None


def _插入映射(連線: object, 表格: str, 項: Mapping[str, object]) -> None:
    if type(項) is not dict or not 項: raise ValueError
    schema欄位 = {
        "endpoint_invocations": ("id","endpoint_id","endpoint_version_id","credential_id","request_id","session_id","message_id","status","input","metadata","output","error","usage","metadata_size_bytes","metadata_sha256","latency_ms","pricing_version","created_at","completed_at"),
        "run_events": ("id","invocation_id","sequence_number","event_type","payload","created_at"),
        "endpoint_tool_calls": ("id","invocation_id","run_event_id","sequence_number","tool_name","arguments","outcome","result","error","latency_ms","retry_of_tool_call_id","created_at"),
        "audit_events": ("id","event_id","occurred_at","action","outcome","actor_type","actor_id","resource_type","resource_id","request_id","endpoint_id","invocation_id","metadata","created_at"),
        "invocation_sensitive_hits": ("id","invocation_id","tool_call_id","target_type","detector_type","json_path","start_offset","end_offset","audit_event_id","detected_at"),
    }[表格]
    JSON欄位 = frozenset({"input","metadata","output","error","usage","payload","arguments","result"})
    公開到schema = {f"{鍵}_json": 鍵 for 鍵 in JSON欄位}
    正規項: dict[str, object] = {}
    for 鍵, 原值 in 項.items():
        schema鍵 = 公開到schema[鍵] if 鍵 in 公開到schema else 鍵
        if schema鍵 not in schema欄位 or schema鍵 in 正規項: raise ValueError
        正規項[schema鍵] = 原值
    資料庫欄位 = tuple(鍵 for 鍵 in schema欄位 if 鍵 in 正規項)
    if set(正規項) != set(資料庫欄位): raise ValueError
    值 = tuple(_JSON(正規項[鍵]) if 鍵 in JSON欄位 and type(正規項[鍵]) is not str else 正規項[鍵]
               for 鍵 in 資料庫欄位)
    位置 = tuple("%s::jsonb" if 鍵 in JSON欄位 else
               "to_timestamp(%s)" if 鍵 in ("created_at","completed_at","occurred_at","detected_at") else "%s"
               for 鍵 in 資料庫欄位)
    游標 = 連線.execute(f"INSERT INTO {表格}({','.join(資料庫欄位)}) VALUES ({','.join(位置)})", 值)
    if getattr(游標, "rowcount", None) != 1: raise ValueError
