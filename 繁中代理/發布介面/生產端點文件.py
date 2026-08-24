"""A23 端點文件的 SQLite safe projection、credential classifier 與 lifecycle wiring。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, RLock
from typing import Protocol

from .端點文件 import 端點文件投影, 渲染端點文件
from .憑證.服務 import SQLite憑證驗證服務, 憑證驗證結果, 憑證驗證狀態

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_失敗 = "端點文件服務失敗"
_未授權 = "文件憑證未授權"


class 文件服務失敗(RuntimeError):
    """SQLite、projection、classifier drift 或 lifecycle 不可用的 fixed failure。"""


class 文件憑證未授權(RuntimeError):
    """所有 key identity/status denial 的單一公開分類。"""


class 文件憑證分類器(Protocol):
    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果: ...


class SQLite憑證文件分類器:
    """只暴露既有 SQLite credential service 的 read-only 驗證；不刷新 last_used。"""

    def __init__(self, service: SQLite憑證驗證服務) -> None:
        if type(service) is not SQLite憑證驗證服務:
            raise ValueError(_失敗) from None
        self._service = service

    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果:
        return self._service.驗證(endpoint_id, presented_api_key)


class SQLite端點文件服務:
    """每次 operation 以 fresh mode=ro BEGIN snapshot 投影 current version safe columns。"""

    def __init__(self, 資料庫路徑: str | Path, 憑證分類器: 文件憑證分類器) -> None:
        if type(資料庫路徑) not in (str, type(Path())):
            raise ValueError(_失敗) from None
        path = Path(資料庫路徑)
        verifier = getattr(憑證分類器, "驗證", None)
        if not path.is_absolute() or not path.name or not callable(verifier):
            raise ValueError(_失敗) from None
        self._uri = path.as_uri() + "?mode=ro"
        self._classifier = 憑證分類器

    def 讀取管理文件(self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 管理者: bool) -> bytes | None:
        """owner 只讀自己、admin 讀任一；foreign/missing 都回 None，歷史狀態可讀。"""
        try:
            _驗證識別(端點識別碼)
            _驗證識別(擁有者使用者識別碼)
            if type(管理者) is not bool:
                raise ValueError
            sql = _投影SQL + " WHERE e.id=?"
            params: tuple[object, ...] = (端點識別碼,)
            if not 管理者:
                sql += " AND e.owner_user_id=?"
                params += (擁有者使用者識別碼,)
            sql += " LIMIT 2"
            with self._snapshot() as connection:
                rows = connection.execute(sql, params).fetchall()
                if type(rows) is not list or len(rows) > 1:
                    raise ValueError
                if not rows:
                    return None
                projection, _ = _重建投影(rows[0])
                return 渲染端點文件(projection)
        except _控制流程:
            raise
        except 文件服務失敗:
            raise
        except BaseException:
            raise 文件服務失敗(_失敗) from None

    def 讀取金鑰文件(self, *, 短名: str, API金鑰: str) -> bytes:
        """slug snapshot 與既有 classifier 結果交叉驗證後才 release active docs。"""
        try:
            if not _短名合法(短名) or type(API金鑰) is not str:
                raise 文件憑證未授權(_未授權)
            with self._snapshot() as connection:
                rows = connection.execute(_投影SQL + " WHERE e.slug=? LIMIT 2", (短名,)).fetchall()
                if type(rows) is not list or len(rows) > 1:
                    raise ValueError
                if not rows:
                    raise 文件憑證未授權(_未授權)
                projection, snapshot = _重建投影(rows[0])
                classified = self._classifier.驗證(snapshot[0], API金鑰)
                if type(classified) is not 憑證驗證結果:
                    raise ValueError
                if classified.status is not 憑證驗證狀態.有效:
                    raise 文件憑證未授權(_未授權)
                observed = (
                    classified.endpoint_id, classified.endpoint_status,
                    classified.current_version_id, classified.endpoint_rate_limit,
                )
                expected = (snapshot[0], snapshot[1], snapshot[2], snapshot[3])
                if observed != expected:
                    raise ValueError
                if snapshot[1] != "active":
                    raise 文件憑證未授權(_未授權)
                return 渲染端點文件(projection)
        except _控制流程:
            raise
        except 文件憑證未授權:
            raise
        except BaseException:
            raise 文件服務失敗(_失敗) from None

    @contextmanager
    def _snapshot(self):
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


_投影SQL = (
    "SELECT e.id,e.owner_user_id,e.slug,e.status,e.current_version_id,"
    "e.rate_limit_requests,e.rate_limit_window_seconds,"
    "v.id,v.endpoint_id,v.version_number,v.input_schema_json,v.response_schema_json "
    "FROM published_endpoints AS e LEFT JOIN published_endpoint_versions AS v "
    "ON v.id=e.current_version_id AND v.endpoint_id=e.id"
)


def _重建投影(row: object) -> tuple[端點文件投影, tuple[str, str, str, int]]:
    if type(row) is not tuple or len(row) != 12:
        raise ValueError
    endpoint, owner, slug, status, current, requests, window, version_id, version_endpoint, number, input_text, response_text = row
    _驗證識別(endpoint); _驗證識別(owner); _驗證識別(current); _驗證識別(version_id); _驗證識別(version_endpoint)
    if current != version_id or endpoint != version_endpoint or not _短名合法(slug):
        raise ValueError
    if type(status) is not str or status not in {"active", "disabled", "archived"}:
        raise ValueError
    if type(number) is not int or not 1 <= number <= 2_147_483_647:
        raise ValueError
    if type(requests) is not int or type(window) is not int:
        raise ValueError
    input_schema = {} if input_text is None else _解析canonical綱要(input_text)
    response_schema = _解析canonical綱要(response_text)
    projection = 端點文件投影(
        端點識別碼=endpoint, 短名=slug, 版本=number, 狀態=status,
        輸入綱要=input_schema, 回應綱要=response_schema,
        端點請求上限=requests, 端點窗口秒數=window,
    )
    return projection, (endpoint, status, current, requests)


def _解析canonical綱要(text: object) -> dict:
    if type(text) is not str or not 2 <= len(text.encode("utf-8")) <= 65_536:
        raise ValueError
    value = json.loads(text)
    if type(value) is not dict:
        raise ValueError
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if canonical != text:
        raise ValueError
    return value


def _驗證識別(value: object) -> None:
    if (type(value) is not str or not 1 <= len(value) <= 128 or value.strip() != value
            or not all(ch.isascii() and (ch.isalnum() or ch in "_.:-") for ch in value)):
        raise ValueError


def _短名合法(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 128 and value.strip() == value
            and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in value))


class 延遲端點文件服務:
    """以 `(service,generation)` lifecycle identity 管理 active leases 與 shared drain。"""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._service = self._draining_service = None
        self._generation = 0
        self._current_generation = self._draining_generation = None
        self._active = 0
        self._draining = False

    def 安裝(self, service: SQLite端點文件服務) -> int:
        if type(service) is not SQLite端點文件服務:
            raise ValueError(_失敗) from None
        with self._condition:
            if self._service is not None or self._active or self._draining_service is not None:
                raise ValueError(_失敗) from None
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
                raise 文件服務失敗(_失敗) from None
            self._active += 1
        try:
            yield service
        finally:
            with self._condition:
                self._active -= 1
                if self._active == 0:
                    self._condition.notify_all()

    def 清除(self, service: SQLite端點文件服務, generation: int) -> None:
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

    def 讀取管理文件(self, **kwargs):
        with self._lease() as service:
            return service.讀取管理文件(**kwargs)

    def 讀取金鑰文件(self, **kwargs):
        with self._lease() as service:
            return service.讀取金鑰文件(**kwargs)


_可信清除 = 延遲端點文件服務.清除


async def 安裝端點文件資源(main_resource, proxy: 延遲端點文件服務, database_path: Path):
    """startup 建立既有 credential verifier adapter，shutdown 先 drain docs 再清主資源。"""
    service = None
    try:
        classifier = SQLite憑證文件分類器(SQLite憑證驗證服務(database_path))
        service = SQLite端點文件服務(database_path, classifier)
        generation = proxy.安裝(service)
        original_cleanup = getattr(main_resource, "_執行關閉同步", None)
        if not callable(original_cleanup):
            raise ValueError(_失敗) from None

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


__all__ = (
    "SQLite端點文件服務", "SQLite憑證文件分類器", "延遲端點文件服務",
    "文件服務失敗", "文件憑證未授權", "安裝端點文件資源",
)
