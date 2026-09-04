"""Owner endpoint list/detail 的唯讀 SQLite production adapter 與 lifespan wiring。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, RLock

from fastapi import Depends

from ..使用者 import 使用者上下文
from .OpenAPI相依權限 import _登錄Canonical相依封裝
from .網頁工作階段 import 網頁使用者
from .路由.端點查詢 import (
    端點列表回應,
    端點列表項目,
    端點安全詳情,
    端點查詢游標錯誤,
)

_查詢失敗 = "端點管理查詢失敗"
_游標失敗 = "端點查詢游標無效"
_領域分隔 = b"testagent2:published-endpoint-query-cursor:v1"
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_狀態 = frozenset(("active", "disabled", "archived"))


class SQLite端點管理查詢服務:
    """每次操作以 fresh mode=ro connection 與單一 BEGIN snapshot 查詢安全投影。"""

    def __init__(self, 資料庫路徑: str | Path, *, 游標簽章金鑰: bytes) -> None:
        """保存 lexical DB authority 與 exact 32-byte signing key；建構期零 I/O。"""
        if type(資料庫路徑) not in (str, type(Path())) or type(游標簽章金鑰) is not bytes or len(游標簽章金鑰) != 32:
            raise ValueError(_查詢失敗) from None
        路徑 = Path(資料庫路徑)
        if not 路徑.is_absolute() or not 路徑.name:
            raise ValueError(_查詢失敗) from None
        self._uri = 路徑.as_uri() + "?mode=ro"
        self._key = bytes(游標簽章金鑰)

    def 列出端點(self, *, 擁有者使用者識別碼: str, 管理者查詢全部: bool,
             數量上限: int, 游標: str | None) -> 端點列表回應:
        """依 owner/all scope 以 updated_at DESC,id ASC 的 keyset cursor 回傳 LIMIT+1。"""
        _驗證識別碼(擁有者使用者識別碼)
        if type(管理者查詢全部) is not bool or type(數量上限) is not int or not 1 <= 數量上限 <= 100:
            raise RuntimeError(_查詢失敗) from None
        範圍 = "all" if 管理者查詢全部 else "owner"
        位置 = None if 游標 is None else self._解碼游標(
            游標, 範圍=範圍, 擁有者=擁有者使用者識別碼,
        )
        條件 = []
        參數: list[object] = []
        if not 管理者查詢全部:
            條件.append("e.owner_user_id=?")
            參數.append(擁有者使用者識別碼)
        if 位置 is not None:
            條件.append("(e.updated_at<? OR (e.updated_at=? AND e.id>?))")
            參數.extend((位置[0], 位置[0], 位置[1]))
        where = " WHERE " + " AND ".join(條件) if 條件 else ""
        sql = (
            "SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,"
            "v.id,v.version_number,e.created_at,e.updated_at "
            "FROM published_endpoints AS e "
            "LEFT JOIN published_endpoint_versions AS v "
            "ON v.id=e.current_version_id AND v.endpoint_id=e.id" + where +
            " ORDER BY e.updated_at DESC,e.id ASC LIMIT ?"
        )
        參數.append(數量上限 + 1)
        try:
            with self._snapshot() as 連線:
                rows = 連線.execute(sql, tuple(參數)).fetchall()
            if type(rows) is not list or len(rows) > 數量上限 + 1:
                raise ValueError
            安全列 = [_重建列(row) for row in rows]
            頁列 = 安全列[:數量上限]
            items = tuple(端點列表項目(
                row[0], row[2], row[3], row[5], row[6], row[8],
            ) for row in 頁列)
            next_cursor = None
            if len(安全列) > 數量上限:
                最後 = 頁列[-1]
                next_cursor = self._編碼游標(
                    範圍=範圍, 擁有者=擁有者使用者識別碼,
                    更新時間=最後[8], 端點識別碼=最後[0],
                )
            return 端點列表回應(items, next_cursor)
        except 端點查詢游標錯誤:
            raise
        except _控制流程:
            raise
        except BaseException:
            raise RuntimeError(_查詢失敗) from None

    def 讀取端點(self, *, 端點識別碼: str, 擁有者使用者識別碼: str,
             管理者查詢全部: bool) -> 端點安全詳情 | None:
        """讀取一筆安全詳情；foreign 與 missing 都回傳 None。"""
        _驗證識別碼(端點識別碼)
        _驗證識別碼(擁有者使用者識別碼)
        if type(管理者查詢全部) is not bool:
            raise RuntimeError(_查詢失敗) from None
        sql = (
            "SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,"
            "v.id,v.version_number,e.created_at,e.updated_at "
            "FROM published_endpoints AS e "
            "LEFT JOIN published_endpoint_versions AS v "
            "ON v.id=e.current_version_id AND v.endpoint_id=e.id WHERE e.id=?"
        )
        params: tuple[object, ...] = (端點識別碼,)
        if not 管理者查詢全部:
            sql += " AND e.owner_user_id=?"
            params += (擁有者使用者識別碼,)
        sql += " LIMIT 2"
        try:
            with self._snapshot() as 連線:
                rows = 連線.execute(sql, params).fetchall()
            if type(rows) is not list or len(rows) > 1:
                raise ValueError
            if not rows:
                return None
            row = _重建列(rows[0])
            return 端點安全詳情(row[0], row[1], row[2], row[3], row[5], row[6], row[7], row[8])
        except _控制流程:
            raise
        except BaseException:
            raise RuntimeError(_查詢失敗) from None

    @contextmanager
    def _snapshot(self):
        """開啟 fresh read-only connection，BEGIN 後無論結果皆 rollback/close。"""
        connection = None
        try:
            connection = sqlite3.connect(self._uri, uri=True, isolation_level=None)
            connection.execute("BEGIN")
            yield connection
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()

    def _編碼游標(self, *, 範圍: str, 擁有者: str, 更新時間: int | float,
              端點識別碼: str) -> str:
        payload = json.dumps(
            {"id": 端點識別碼, "owner": 擁有者, "scope": 範圍,
             "updated_at": 更新時間, "version": 1},
            ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
        mac = hmac.new(self._key, _領域分隔 + b"\0" + payload, hashlib.sha256).digest()
        cursor = base64.urlsafe_b64encode(payload + mac).rstrip(b"=").decode("ascii")
        if len(cursor) > 512:
            raise RuntimeError(_查詢失敗) from None
        return cursor

    def _解碼游標(self, cursor: str, *, 範圍: str, 擁有者: str) -> tuple[int | float, str]:
        try:
            if (type(cursor) is not str or not 1 <= len(cursor) <= 512
                    or not all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in cursor)):
                raise ValueError
            padded = cursor + "=" * ((4 - len(cursor) % 4) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != cursor or len(raw) <= 32:
                raise ValueError
            payload, supplied = raw[:-32], raw[-32:]
            expected = hmac.new(self._key, _領域分隔 + b"\0" + payload, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            value = json.loads(payload)
            if (type(value) is not dict or set(value) != {"version", "scope", "owner", "updated_at", "id"}
                    or value["version"] != 1 or type(value["version"]) is not int
                    or value["scope"] != 範圍 or value["owner"] != 擁有者):
                raise ValueError
            canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
            if canonical != payload:
                raise ValueError
            _驗證識別碼(value["owner"]); _驗證識別碼(value["id"])
            _驗證時間(value["updated_at"])
            return value["updated_at"], value["id"]
        except _控制流程:
            raise
        except BaseException:
            raise 端點查詢游標錯誤(_游標失敗) from None


class 延遲端點管理查詢服務:
    """generation-safe lazy provider slot，shutdown 先撤銷再等待 active leases。"""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._service = self._draining_service = None
        self._generation = 0
        self._current_generation = self._draining_generation = None
        self._active = 0
        self._draining = False

    def 安裝(self, service: object) -> int:
        from .治理.PostgreSQL端點管理查詢服務 import PostgreSQL端點管理查詢服務
        if type(service) not in (SQLite端點管理查詢服務, PostgreSQL端點管理查詢服務):
            raise ValueError("Published端點查詢服務無效") from None
        with self._condition:
            if self._service is not None or self._active:
                raise ValueError("Published端點查詢服務無效") from None
            self._generation += 1
            self._current_generation = self._generation
            self._service = service
            self._draining = False
            return self._generation

    @contextmanager
    def _lease(self):
        with self._condition:
            service = self._service
            if service is None or self._draining:
                raise RuntimeError("Published端點查詢服務不可用") from None
            self._active += 1
        try:
            yield service
        finally:
            with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    def 清除(self, service: SQLite端點管理查詢服務, generation: int) -> None:
        with self._condition:
            if self._draining_service is service and self._draining_generation == generation:
                while self._draining_service is service and self._draining_generation == generation:
                    self._condition.wait()
                return
            if self._service is service and self._current_generation == generation:
                self._draining = True
                self._draining_service, self._draining_generation = service, generation
                self._service = self._current_generation = None
                while self._active:
                    self._condition.wait()
                self._draining_service = self._draining_generation = None
                self._draining = False
                self._condition.notify_all()

    def 列出端點(self, **kwargs):
        with self._lease() as service:
            return service.列出端點(**kwargs)

    def 讀取端點(self, **kwargs):
        with self._lease() as service:
            return service.讀取端點(**kwargs)


_可信清除 = 延遲端點管理查詢服務.清除


def 建立端點管理身份相依(目前工作階段相依):
    """只從 canonical cookie-session dependency 重建既有 route 所需 identity DTO。"""
    if not callable(目前工作階段相依):
        raise ValueError("Published端點查詢服務無效") from None

    def 取得身份(使用者=Depends(目前工作階段相依)) -> 使用者上下文:
        if type(使用者) is not 網頁使用者:
            raise RuntimeError("Published端點查詢服務不可用") from None
        try:
            identifier = object.__getattribute__(使用者, "識別碼")
            role = object.__getattribute__(使用者, "角色")
            _驗證識別碼(identifier)
            if type(role) is not str or role not in {"member", "admin"}:
                raise ValueError
            return 使用者上下文(user_id=identifier, is_admin=role == "admin")
        except _控制流程:
            raise
        except BaseException:
            raise RuntimeError("Published端點查詢服務不可用") from None

    setattr(取得身份, "__canonical_dependency__", 目前工作階段相依)
    _登錄Canonical相依封裝(取得身份, 目前工作階段相依)
    return 取得身份


def 衍生端點查詢游標金鑰(deployment_key: bytes) -> bytes:
    """由既有 Owner 觀測 deployment key 做 domain-separated HMAC 派生。"""
    if type(deployment_key) is not bytes or len(deployment_key) != 32:
        raise ValueError("Published端點查詢服務無效") from None
    return hmac.new(deployment_key, _領域分隔, hashlib.sha256).digest()


async def 安裝端點查詢資源(main_resource, proxy: 延遲端點管理查詢服務,
                    database_path: Path, deployment_key: bytes):
    """startup 安裝 adapter，shutdown 先 generation-safe drain 再交還既有唯一 owner。"""
    service = None
    try:
        service = SQLite端點管理查詢服務(
            database_path, 游標簽章金鑰=衍生端點查詢游標金鑰(deployment_key),
        )
        generation = proxy.安裝(service)
        try:
            original_cleanup = object.__getattribute__(main_resource, "_執行關閉同步")
        except AttributeError:
            original_cleanup = None
        if not callable(original_cleanup):
            raise ValueError("Published端點查詢資源無效") from None

        def cleanup() -> None:
            control = ordinary = None
            for operation in (
                lambda: proxy.清除(service, generation),
                lambda: _可信清除(proxy, service, generation),
                original_cleanup,
            ):
                try:
                    operation()
                except BaseException as error:
                    if isinstance(error, _控制流程):
                        if control is None: control = error
                    elif ordinary is None:
                        ordinary = error
            if control is not None: raise control
            if ordinary is not None: raise ordinary

        main_resource._執行關閉同步 = cleanup
        return main_resource
    except BaseException:
        if service is not None:
            with proxy._condition:
                generation = proxy._current_generation if proxy._service is service else None
            if type(generation) is int:
                _可信清除(proxy, service, generation)
        try:
            await main_resource.關閉()
        except BaseException:
            pass
        raise


def _重建列(row: object) -> tuple[object, ...]:
    if type(row) is not tuple or len(row) != 9:
        raise ValueError
    endpoint, owner, slug, status, current, version, number, created, updated = row
    _驗證識別碼(endpoint); _驗證識別碼(owner)
    if (type(slug) is not str or not 1 <= len(slug) <= 256 or slug.strip() != slug
            or any(ord(character) < 32 for character in slug)):
        raise ValueError
    if type(status) is not str or status not in _狀態:
        raise ValueError
    if current is None:
        if version is not None or number is not None:
            raise ValueError
    else:
        _驗證識別碼(current)
        if version != current:
            raise ValueError
        _驗證識別碼(version)
        if type(number) is not int or not 1 <= number <= 2_147_483_647:
            raise ValueError
    _驗證時間(created); _驗證時間(updated)
    return row


def _驗證識別碼(value: object) -> None:
    if (type(value) is not str or not 1 <= len(value) <= 128 or value.strip() != value
            or not all(ch.isascii() and (ch.isalnum() or ch in "_.:-") for ch in value)):
        raise ValueError


def _驗證時間(value: object) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError


__all__ = (
    "SQLite端點管理查詢服務", "延遲端點管理查詢服務", "建立端點管理身份相依",
    "衍生端點查詢游標金鑰", "安裝端點查詢資源",
)
