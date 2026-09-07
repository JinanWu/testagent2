"""PostgreSQL endpoint list/detail safe projection provider。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from collections.abc import Mapping

from 繁中代理.PostgreSQL連線 import 交易連線
from ..路由.端點查詢 import 端點列表回應, 端點列表項目, 端點安全詳情, 端點查詢游標錯誤

_失敗 = "端點管理查詢失敗"
_游標失敗 = "端點查詢游標無效"
_領域 = b"testagent2:published-endpoint-query-cursor:v1"
_狀態 = frozenset(("active", "disabled", "archived"))


class PostgreSQL端點管理查詢服務:
    """以 fresh PostgreSQL transaction 執行 owner/admin list/detail，回傳 canonical route DTO。"""
    __slots__ = ("_設定", "_key")

    def __init__(self, 凍結設定: object, *, 游標簽章金鑰: bytes) -> None:
        if type(游標簽章金鑰) is not bytes or len(游標簽章金鑰) != 32:
            raise ValueError(_失敗) from None
        self._設定, self._key = 凍結設定, bytes(游標簽章金鑰)

    def 列出端點(self, *, 擁有者使用者識別碼: str, 管理者查詢全部: bool,
             數量上限: int, 游標: str | None) -> 端點列表回應:
        _id(擁有者使用者識別碼)
        if type(管理者查詢全部) is not bool or type(數量上限) is not int or not 1 <= 數量上限 <= 100:
            raise RuntimeError(_失敗) from None
        scope = "all" if 管理者查詢全部 else "owner"
        position = None if 游標 is None else self._解碼(游標, scope, 擁有者使用者識別碼)
        terms, params = [], []
        if not 管理者查詢全部:
            terms.append("e.owner_user_id=%s"); params.append(擁有者使用者識別碼)
        if position is not None:
            terms.append("(e.updated_at<to_timestamp(%s) OR (e.updated_at=to_timestamp(%s) AND e.id>%s))")
            params.extend((position[0], position[0], position[1]))
        where = " WHERE " + " AND ".join(terms) if terms else ""
        sql = ("SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,v.id AS version_id,v.version_number,"
               "EXTRACT(EPOCH FROM e.created_at)::double precision AS created_at_epoch,"
               "EXTRACT(EPOCH FROM e.updated_at)::double precision AS updated_at_epoch "
               "FROM published_endpoints e LEFT JOIN published_endpoint_versions v "
               "ON v.id=e.current_version_id AND v.endpoint_id=e.id" + where +
               " ORDER BY e.updated_at DESC,e.id ASC LIMIT %s")
        params.append(數量上限 + 1)
        try:
            with 交易連線(self._設定) as connection:
                rows = connection.execute(sql, tuple(params)).fetchall()
            safe = tuple(_row(row) for row in rows)
            if len(safe) > 數量上限 + 1: raise ValueError
            page = safe[:數量上限]
            items = tuple(端點列表項目(r[0], r[2], r[3], r[5], r[6], r[8]) for r in page)
            nxt = None
            if len(safe) > 數量上限:
                last = page[-1]
                nxt = self._編碼(scope, 擁有者使用者識別碼, last[8], last[0])
            return 端點列表回應(items, nxt)
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 端點查詢游標錯誤): raise
        except BaseException: raise RuntimeError(_失敗) from None

    def 讀取端點(self, *, 端點識別碼: str, 擁有者使用者識別碼: str,
             管理者查詢全部: bool) -> 端點安全詳情 | None:
        _id(端點識別碼); _id(擁有者使用者識別碼)
        if type(管理者查詢全部) is not bool: raise RuntimeError(_失敗) from None
        sql = ("SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,v.id AS version_id,v.version_number,"
               "EXTRACT(EPOCH FROM e.created_at)::double precision AS created_at_epoch,"
               "EXTRACT(EPOCH FROM e.updated_at)::double precision AS updated_at_epoch "
               "FROM published_endpoints e LEFT JOIN published_endpoint_versions v "
               "ON v.id=e.current_version_id AND v.endpoint_id=e.id WHERE e.id=%s")
        params: tuple[object, ...] = (端點識別碼,)
        if not 管理者查詢全部:
            sql += " AND e.owner_user_id=%s"; params += (擁有者使用者識別碼,)
        sql += " LIMIT 2"
        try:
            with 交易連線(self._設定) as connection:
                rows = connection.execute(sql, params).fetchall()
            if len(rows) > 1: raise ValueError
            if not rows: return None
            r = _row(rows[0])
            return 端點安全詳情(r[0], r[1], r[2], r[3], r[5], r[6], r[7], r[8])
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise RuntimeError(_失敗) from None

    def _編碼(self, scope: str, owner: str, updated: int | float, endpoint: str) -> str:
        payload = json.dumps({"id": endpoint, "owner": owner, "scope": scope, "updated_at": updated, "version": 1},
                             sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
        mac = hmac.digest(self._key, _領域 + b"\0" + payload, "sha256")
        return base64.urlsafe_b64encode(payload + mac).rstrip(b"=").decode("ascii")

    def _解碼(self, cursor: str, scope: str, owner: str) -> tuple[int | float, str]:
        try:
            if type(cursor) is not str or not 1 <= len(cursor) <= 512: raise ValueError
            raw = base64.b64decode(cursor + "=" * ((4-len(cursor)%4)%4), altchars=b"-_", validate=True)
            payload, supplied = raw[:-32], raw[-32:]
            if len(payload) == 0 or not hmac.compare_digest(supplied, hmac.digest(self._key, _領域+b"\0"+payload, "sha256")): raise ValueError
            value = json.loads(payload)
            if (type(value) is not dict or set(value) != {"id","owner","scope","updated_at","version"}
                    or value["version"] != 1 or value["scope"] != scope or value["owner"] != owner): raise ValueError
            if json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii") != payload: raise ValueError
            _id(value["id"]); _time(value["updated_at"])
            return value["updated_at"], value["id"]
        except (KeyboardInterrupt, SystemExit, GeneratorExit): raise
        except BaseException: raise 端點查詢游標錯誤(_游標失敗) from None


def _normal(row: object, names: tuple[str, ...]) -> tuple[object, ...]:
    if isinstance(row, Mapping):
        if set(row) != set(names): raise ValueError
        return tuple(row[name] for name in names)
    if type(row) is tuple and len(row) == len(names): return row
    raise ValueError


def _row(row: object) -> tuple[object, ...]:
    r = _normal(row, ("id","owner_user_id","slug","status","current_version_id","version_id","version_number","created_at_epoch","updated_at_epoch"))
    _id(r[0]); _id(r[1]); _time(r[7]); _time(r[8])
    if type(r[2]) is not str or not 1 <= len(r[2]) <= 256 or r[2].strip() != r[2]: raise ValueError
    if r[3] not in _狀態: raise ValueError
    if r[4] is None:
        if r[5] is not None or r[6] is not None: raise ValueError
    else:
        _id(r[4]); _id(r[5])
        if r[4] != r[5] or type(r[6]) is not int or not 1 <= r[6] <= 2_147_483_647: raise ValueError
    return r


def _id(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= 128 or value.strip() != value or not all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value): raise ValueError


def _time(value: object) -> None:
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0: raise ValueError


__all__ = ("PostgreSQL端點管理查詢服務",)
