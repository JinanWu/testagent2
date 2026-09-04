"""PostgreSQL endpoint documentation projection and credential classifier。"""
from __future__ import annotations

import json
from collections.abc import Mapping

from 繁中代理.PostgreSQL連線 import 交易連線
from ..端點文件 import 端點文件投影, 渲染端點文件
from ..憑證.PostgreSQL儲存庫 import PostgreSQL憑證儲存庫
from ..憑證.服務 import 憑證驗證結果, 憑證驗證狀態
from ..生產端點文件 import 文件憑證未授權, 文件服務失敗

_失敗 = "端點文件服務失敗"
_未授權 = "文件憑證未授權"


class PostgreSQL憑證文件分類器:
    """密封在 exact PostgreSQL credential repository 上的唯讀分類契約。"""
    __slots__ = ("_儲存庫",)

    def __init__(self, 儲存庫: PostgreSQL憑證儲存庫) -> None:
        if type(儲存庫) is not PostgreSQL憑證儲存庫:
            raise ValueError(_失敗) from None
        self._儲存庫 = 儲存庫

    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果:
        return PostgreSQL憑證儲存庫.驗證(self._儲存庫, endpoint_id, presented_api_key)


class PostgreSQL端點文件服務:
    """以 PostgreSQL transaction 投影 current version safe docs，並交叉驗證 typed credential DTO。"""
    __slots__ = ("_設定", "_分類器")

    def __init__(self, 凍結設定: object, 憑證分類器: PostgreSQL憑證文件分類器) -> None:
        if type(憑證分類器) is not PostgreSQL憑證文件分類器:
            raise ValueError(_失敗) from None
        self._設定, self._分類器 = 凍結設定, 憑證分類器

    def 讀取管理文件(self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 管理者: bool) -> bytes | None:
        try:
            _id(端點識別碼); _id(擁有者使用者識別碼)
            if type(管理者) is not bool: raise ValueError
            sql = _SQL + " WHERE e.id=%s"
            params: tuple[object,...] = (端點識別碼,)
            if not 管理者: sql += " AND e.owner_user_id=%s"; params += (擁有者使用者識別碼,)
            sql += " LIMIT 2"
            with 交易連線(self._設定) as connection:
                rows = connection.execute(sql, params).fetchall()
            if len(rows) > 1: raise ValueError
            if not rows: return None
            projection, _ = _projection(rows[0])
            return 渲染端點文件(projection)
        except (KeyboardInterrupt,SystemExit,GeneratorExit): raise
        except BaseException: raise 文件服務失敗(_失敗) from None

    def 讀取金鑰文件(self, *, 短名: str, API金鑰: str) -> bytes:
        try:
            if not _slug(短名) or type(API金鑰) is not str: raise 文件憑證未授權(_未授權)
            with 交易連線(self._設定) as connection:
                rows = connection.execute(_SQL + " WHERE e.slug=%s LIMIT 2", (短名,)).fetchall()
            if len(rows) != 1: raise 文件憑證未授權(_未授權)
            projection, snapshot = _projection(rows[0])
            classified = self._分類器.驗證(snapshot[0], API金鑰)
            if (type(classified) is not 憑證驗證結果 or classified.status is not 憑證驗證狀態.有效
                    or (classified.endpoint_id, classified.endpoint_status, classified.current_version_id,
                        classified.endpoint_rate_limit) != snapshot or snapshot[1] != "active"):
                raise 文件憑證未授權(_未授權)
            return 渲染端點文件(projection)
        except (KeyboardInterrupt,SystemExit,GeneratorExit): raise
        except BaseException as error:
            if type(error) is 文件憑證未授權 and error.args == (_未授權,): raise
            raise 文件服務失敗(_失敗) from None


_SQL = ("SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,e.rate_limit_requests,"
        "e.rate_limit_window_seconds,v.id AS version_id,v.endpoint_id AS version_endpoint_id,"
        "v.version_number,v.input_schema AS input_schema_json,v.response_schema AS response_schema_json "
        "FROM published_endpoints e LEFT JOIN published_endpoint_versions v "
        "ON v.id=e.current_version_id AND v.endpoint_id=e.id")
_NAMES = ("id","owner_user_id","slug","status","current_version_id","rate_limit_requests",
          "rate_limit_window_seconds","version_id","version_endpoint_id","version_number",
          "input_schema_json","response_schema_json")


def _projection(row: object) -> tuple[端點文件投影, tuple[str,str,str,int]]:
    r = _normal(row)
    endpoint, owner, slug, status, current, requests, window, version, version_endpoint, number, input_value, response_value = r
    for value in (endpoint, owner, current, version, version_endpoint): _id(value)
    if current != version or endpoint != version_endpoint or not _slug(slug) or status not in {"active","disabled","archived"}: raise ValueError
    if type(number) is not int or not 1 <= number <= 2_147_483_647 or type(requests) is not int or type(window) is not int: raise ValueError
    input_schema = {} if input_value is None else _json_object(input_value)
    response_schema = _json_object(response_value)
    return (端點文件投影(端點識別碼=endpoint, 短名=slug, 版本=number, 狀態=status,
            輸入綱要=input_schema, 回應綱要=response_schema, 端點請求上限=requests, 端點窗口秒數=window),
            (endpoint,status,current,requests))


def _normal(row: object) -> tuple[object,...]:
    if isinstance(row, Mapping):
        if set(row) != set(_NAMES): raise ValueError
        return tuple(row[name] for name in _NAMES)
    if type(row) is tuple and len(row) == len(_NAMES): return row
    raise ValueError


def _json_object(value: object) -> dict:
    if type(value) is str: value = json.loads(value)
    if type(value) is not dict: raise ValueError
    # psycopg may already decode jsonb; detach through canonical JSON.
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",",":"), allow_nan=False))


def _id(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= 128 or value.strip() != value or not all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value): raise ValueError


def _slug(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 128 and value.strip() == value and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)


__all__ = ("PostgreSQL憑證文件分類器", "PostgreSQL端點文件服務")
