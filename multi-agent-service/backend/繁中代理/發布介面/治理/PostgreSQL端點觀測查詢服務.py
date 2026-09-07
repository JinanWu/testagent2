"""PostgreSQL owner metrics 與安全 diagnostics 查詢服務。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections.abc import Mapping
from types import BuiltinFunctionType, FunctionType
from typing import Callable

from 繁中代理.PostgreSQL連線 import 交易連線
from .觀測契約 import (
    指標查詢成功, 診斷查詢成功, 端點不可見結果,
    診斷用量, 診斷項目, 診斷頁,
)
from .觀測供應器 import _建立指標, 端點觀測查詢錯誤, 端點觀測游標錯誤

_領域 = b"testagent2:postgres-owner-diagnostics:v1"
_狀態 = frozenset(("pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"))
_欄位 = (
    "id", "request_id", "endpoint_version_id", "status", "error_code", "latency_ms",
    "usage", "created_at_epoch", "completed_at_epoch",
)


class PostgreSQL端點觀測查詢服務:
    """以 fresh PostgreSQL transaction 產生 owner-scoped metrics 與 diagnostics。"""

    __slots__ = ("_設定", "_時鐘", "_key")

    def __init__(self, 凍結設定: object, *, 時鐘: Callable[[], float] = time.time,
                 游標簽章金鑰: bytes) -> None:
        if (type(時鐘) not in (FunctionType, BuiltinFunctionType)
                or type(游標簽章金鑰) is not bytes or len(游標簽章金鑰) < 32):
            raise ValueError("PostgreSQL端點觀測查詢服務無效") from None
        self._設定, self._時鐘, self._key = 凍結設定, 時鐘, bytes(游標簽章金鑰)

    def 讀取端點指標(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                     端點識別碼: str, 視窗秒數: int):
        try:
            開始, 結束 = self._驗證視窗(擁有者使用者識別碼, 是否管理者, 端點識別碼, 視窗秒數)
            with 交易連線(self._設定) as connection:
                if not self._可見(connection, 擁有者使用者識別碼, 是否管理者, 端點識別碼):
                    return 端點不可見結果()
                rows = connection.execute(
                    "SELECT EXTRACT(EPOCH FROM i.created_at)::double precision AS created_at_epoch,"
                    "i.status,i.latency_ms,i.usage,i.pricing_version,se.error_code,"
                    "EXISTS(SELECT 1 FROM endpoint_redactions r WHERE r.invocation_id=i.id "
                    "AND r.target_type='error') AS error_redacted "
                    "FROM endpoint_invocations i LEFT JOIN endpoint_invocation_safe_errors se "
                    "ON se.invocation_id=i.id WHERE i.endpoint_id=%s "
                    "AND i.created_at>=to_timestamp(%s) AND i.created_at<to_timestamp(%s) "
                    "ORDER BY i.id LIMIT 4097",
                    (端點識別碼, 開始, 結束),
                ).fetchall()
            if len(rows) > 4096:
                raise ValueError
            normalized = tuple(self._指標列(row) for row in rows)
            return 指標查詢成功(_建立指標(端點識別碼, 開始, 結束, normalized))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 端點觀測查詢錯誤("端點觀測不可取得") from None

    def 列出端點診斷(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                     端點識別碼: str, 視窗秒數: int, 數量上限: int,
                     游標: str | None):
        try:
            開始, 結束 = self._驗證視窗(擁有者使用者識別碼, 是否管理者, 端點識別碼, 視窗秒數)
            if type(數量上限) is not int or not 1 <= 數量上限 <= 100:
                raise ValueError
            position = None if 游標 is None else self._解碼游標(
                游標, 擁有者使用者識別碼, 端點識別碼,
            )
            predicate = ""
            params: list[object] = [端點識別碼, 開始, 結束]
            if position is not None:
                predicate = " AND (i.created_at<to_timestamp(%s) OR (i.created_at=to_timestamp(%s) AND i.id<%s))"
                params.extend((position[0], position[0], position[1]))
            params.append(數量上限 + 1)
            with 交易連線(self._設定) as connection:
                if not self._可見(connection, 擁有者使用者識別碼, 是否管理者, 端點識別碼):
                    return 端點不可見結果()
                rows = connection.execute(
                    "SELECT i.id,i.request_id,i.endpoint_version_id,i.status,se.error_code,i.latency_ms,"
                    "i.usage,EXTRACT(EPOCH FROM i.created_at)::double precision AS created_at_epoch,"
                    "EXTRACT(EPOCH FROM i.completed_at)::double precision AS completed_at_epoch "
                    "FROM endpoint_invocations i LEFT JOIN endpoint_invocation_safe_errors se "
                    "ON se.invocation_id=i.id WHERE i.endpoint_id=%s "
                    "AND i.created_at>=to_timestamp(%s) AND i.created_at<to_timestamp(%s)" + predicate +
                    " ORDER BY i.created_at DESC,i.id DESC LIMIT %s",
                    tuple(params),
                ).fetchall()
                page_rows = rows[:數量上限]
                items = tuple(self._診斷項目(connection, row) for row in page_rows)
            next_cursor = None
            if len(rows) > 數量上限:
                last = self._row(page_rows[-1])
                next_cursor = self._編碼游標(
                    擁有者使用者識別碼, 端點識別碼, last[7], last[0],
                )
            return 診斷查詢成功(診斷頁(items, next_cursor))
        except 端點觀測游標錯誤:
            raise
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 端點觀測查詢錯誤("端點觀測不可取得") from None

    def _驗證視窗(self, owner: str, admin: bool, endpoint: str, window: int) -> tuple[float, float]:
        if (not _id(owner) or type(admin) is not bool or not _id(endpoint)
                or type(window) is not int or not 1 <= window <= 2_592_000):
            raise ValueError
        end = self._時鐘()
        if type(end) not in (int, float) or not math.isfinite(end) or end < window:
            raise ValueError
        return float(end) - window, float(end)

    @staticmethod
    def _可見(connection, owner: str, admin: bool, endpoint: str) -> bool:
        rows = connection.execute(
            "SELECT id FROM published_endpoints WHERE id=%s AND (%s OR owner_user_id=%s) LIMIT 2",
            (endpoint, admin, owner),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError
        if rows:
            value = rows[0]["id"] if isinstance(rows[0], Mapping) else rows[0][0]
            if value != endpoint:
                raise ValueError
        return len(rows) == 1

    @staticmethod
    def _指標列(row: object) -> tuple[object, ...]:
        names = ("created_at_epoch", "status", "latency_ms", "usage", "pricing_version", "error_code", "error_redacted")
        values = _normal(row, names)
        usage = values[3]
        if usage is not None and type(usage) is not str:
            usage = json.dumps(usage, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return (values[0], values[1], values[2], usage, values[4], values[5], int(bool(values[6])))

    def _診斷項目(self, connection, row: object) -> 診斷項目:
        values = self._row(row)
        if values[3] not in _狀態:
            raise ValueError
        usage_value = values[6]
        usage = None
        if usage_value is not None:
            if type(usage_value) is str:
                usage_value = json.loads(usage_value)
            if type(usage_value) is not dict or type(usage_value.get("total_tokens")) is not int:
                raise ValueError
            usage = 診斷用量(usage_value["total_tokens"])
        tool_rows = connection.execute(
            "SELECT tool_name FROM endpoint_tool_calls WHERE invocation_id=%s ORDER BY tool_name",
            (values[0],),
        ).fetchall()
        tools = tuple(sorted({_single(item, "tool_name") for item in tool_rows}))
        redacted = connection.execute(
            "SELECT 1 AS marker FROM endpoint_redactions WHERE invocation_id=%s "
            "AND target_type='error' LIMIT 1", (values[0],),
        ).fetchone()
        hidden = ("error_code", "schema_path") if redacted is not None else ()
        return 診斷項目(
            values[0], values[1], values[2], values[3], None if hidden else values[4],
            None, values[5], usage, tools, values[7], values[8], hidden,
        )

    @staticmethod
    def _row(row: object) -> tuple[object, ...]:
        values = _normal(row, _欄位)
        if not all(_id(value) for value in values[:3]):
            raise ValueError
        return values

    def _編碼游標(self, owner: str, endpoint: str, created: object, invocation: object) -> str:
        if type(created) not in (int, float) or not math.isfinite(created) or not _id(invocation):
            raise ValueError
        payload = json.dumps(
            {"created_at": created, "endpoint": endpoint, "id": invocation, "owner": owner, "version": 1},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
        mac = hmac.digest(self._key, _領域 + b"\0" + payload, "sha256")
        return base64.urlsafe_b64encode(payload + mac).rstrip(b"=").decode("ascii")

    def _解碼游標(self, cursor: str, owner: str, endpoint: str) -> tuple[float, str]:
        try:
            if type(cursor) is not str or not 1 <= len(cursor) <= 1024:
                raise ValueError
            raw = base64.b64decode(cursor + "=" * ((4 - len(cursor) % 4) % 4), altchars=b"-_", validate=True)
            payload, supplied = raw[:-32], raw[-32:]
            expected = hmac.digest(self._key, _領域 + b"\0" + payload, "sha256")
            if not payload or not hmac.compare_digest(supplied, expected):
                raise ValueError
            value = json.loads(payload)
            if (type(value) is not dict or set(value) != {"created_at", "endpoint", "id", "owner", "version"}
                    or value["version"] != 1 or value["owner"] != owner or value["endpoint"] != endpoint
                    or not _id(value["id"]) or type(value["created_at"]) not in (int, float)
                    or not math.isfinite(value["created_at"])):
                raise ValueError
            canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
            if canonical != payload:
                raise ValueError
            return float(value["created_at"]), value["id"]
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 端點觀測游標錯誤("端點觀測不可取得") from None


def _normal(row: object, names: tuple[str, ...]) -> tuple[object, ...]:
    if isinstance(row, Mapping):
        if set(row) != set(names):
            raise ValueError
        return tuple(row[name] for name in names)
    if type(row) is tuple and len(row) == len(names):
        return row
    raise ValueError


def _single(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        if set(row) != {name}:
            raise ValueError
        return row[name]
    if type(row) is tuple and len(row) == 1:
        return row[0]
    raise ValueError


def _id(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 128 and not any(char.isspace() for char in value)


__all__ = ("PostgreSQL端點觀測查詢服務",)
