"""PUB P05 既有發布端點的不可變版本配置服務。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, NoReturn, Protocol

_JSON文字上限 = 1024 * 1024
_JSON節點上限 = 10_000
_JSON深度上限 = 64
from .端點發布 import (
    發布版本快照,
    端點發布輸入錯誤,
    _schema指紋,
    _正規JSON,
    _是有限非負,
    _是識別,
    _安全回滾,
    _安全關閉,
    _拋出清理控制,
    _清除例外鏈,
    _遷移ledger,
    _重建版本快照,
    _驗證已開啟資料庫路徑,
    _驗證既有資料庫路徑,
)
from .綱要 import _slug格式
class 版本配置輸入錯誤(ValueError):
    """代表 P05 scalar 或 prepared snapshot 不符合固定契約。"""
class 版本存取錯誤(PermissionError):
    """代表端點不存在、不屬於 actor，或不是 active。"""


class 版本配置錯誤(RuntimeError):
    """代表版本交易無法完整且耐久地完成。"""


class 版本啟用輸入錯誤(ValueError):
    """代表 P06 啟用純量或 callback contract 無效。"""


class 版本啟用存取錯誤(PermissionError):
    """代表啟用端點不存在、非 owner 或非 active。"""


class 版本啟用錯誤(RuntimeError):
    """代表 pointer 與 audit 無法原子啟用。"""


class 目前版本解析錯誤(LookupError):
    """代表目前版本解析遇到資料、schema、路徑或交易失敗。"""


class 目前版本不存在錯誤(目前版本解析錯誤):
    """代表 authoritative JOIN 找不到 active current version。"""


class BundlePublicationVerifier(Protocol):
    """唯讀、冪等地證明 exact candidate bundle 已發布。"""

    def __call__(self, manifest: dict[str, Any], version_id: str, endpoint_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class 版本啟用結果:
    """成功提交後的 immutable pointer/audit receipt。"""

    endpoint_id: str
    old_version_id: str | None
    new_version_id: str
    version_number: int
    audit_id: str
    activated_at: float

    def __post_init__(self) -> None:
        if (type(self) is not 版本啟用結果 or not _是識別(self.endpoint_id)
                or (self.old_version_id is not None and not _是識別(self.old_version_id))
                or not _是識別(self.new_version_id) or not _是識別(self.audit_id)
                or type(self.version_number) is not int or self.version_number <= 0
                or not _是有限非負(self.activated_at)):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None


@dataclass(frozen=True, slots=True)
class 已釘選版本:
    """只保存 immutable scalar/canonical bytes；每次取快照皆重新 detached。"""

    endpoint_id: str
    service_account_id: str
    version_id: str
    version_number: int
    schema_changed: bool
    created_at: float
    _版本JSON: str = field(repr=False)

    def __post_init__(self) -> None:
        snapshot = None
        失敗 = False
        try:
            if (type(self) is not 已釘選版本 or not _是識別(self.endpoint_id)
                    or not _是識別(self.service_account_id) or not _是識別(self.version_id)
                    or type(self.version_number) is not int or self.version_number <= 0
                    or type(self.schema_changed) is not bool or not _是有限非負(self.created_at)):
                raise ValueError
            snapshot = self.取得版本快照()
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
            _清除例外鏈(控制)
            snapshot = None
            del self, snapshot, 失敗, 控制
            raise
        except BaseException:
            snapshot = None
            失敗 = True
        if 失敗:
            del self, snapshot, 失敗
            raise 目前版本解析錯誤("目前版本解析失敗") from None
        del snapshot, 失敗

    def 取得版本快照(self) -> 發布版本快照:
        """重驗全部固定 slot，再從 canonical bytes 建立全新快照。"""
        payload = result = None
        failed = False
        try:
            if (type(self) is not 已釘選版本 or not _是識別(self.endpoint_id)
                    or not _是識別(self.service_account_id) or not _是識別(self.version_id)
                    or type(self.version_number) is not int or self.version_number <= 0
                    or type(self.schema_changed) is not bool or not _是有限非負(self.created_at)
                    or type(self._版本JSON) is not str):
                raise ValueError
            payload = _解析正規物件(self._版本JSON)
            result = 發布版本快照(**payload)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            if type(payload) is dict:
                payload.clear()
            del self, payload, result, failed
            raise
        except BaseException:
            failed = True
        if type(payload) is dict:
            payload.clear()
        if failed or result is None:
            del self, payload, result, failed
            raise 目前版本解析錯誤("目前版本解析失敗") from None
        del self, payload, failed
        return result
@dataclass(frozen=True, slots=True)
class 版本配置結果:
    """版本配置後只回傳非敏感、不可變的配置識別。"""

    version_id: str
    endpoint_id: str
    version_number: int
    schema_changed: bool
    created_at: float

    def __post_init__(self) -> None:
        if (
            type(self) is not 版本配置結果
            or not _是識別(self.version_id)
            or not _是識別(self.endpoint_id)
            or type(self.version_number) is not int
            or self.version_number <= 0
            or type(self.schema_changed) is not bool
            or not _是有限非負(self.created_at)
        ):
            _拒絕輸入()
class SQLite版本配置服務:
    """以 BEGIN IMMEDIATE 配置下一個 create-only immutable version。"""

    def __init__(
        self,
        database_path: str | Path,
        version_id_factory: Callable[[], str],
        clock: Callable[[], float],
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        self._資料庫路徑 = database_path
        self._版本識別工廠 = version_id_factory
        self._時鐘 = clock
        self._連線工廠 = connection_factory

    def 配置(
        self, owner_user_id: str, endpoint_id: str, prepared_snapshot: 發布版本快照,
    ) -> 版本配置結果:
        """重建 prepared DTO，再於單一鎖定交易授權、配置與提交。"""
        snapshot = path = identity = uri = connection = result = None
        輸入失敗 = 配置失敗 = False
        try:
            if not _是識別(owner_user_id) or not _是識別(endpoint_id):
                輸入失敗 = True
            else:
                snapshot = _重建版本快照(prepared_snapshot)
                if snapshot.created_by_user_id != owner_user_id:
                    輸入失敗 = True
            if not 輸入失敗:
                path, identity = _驗證既有資料庫路徑(self._資料庫路徑)
                uri = path.as_uri() + "?mode=rw"
                connection = self._連線工廠(uri, uri=True, timeout=30.0, isolation_level=None)
                _驗證已開啟資料庫路徑(connection, path, identity)
                result = _配置交易(
                    connection, owner_user_id, endpoint_id, snapshot,
                    self._版本識別工廠, self._時鐘,
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, owner_user_id, endpoint_id, prepared_snapshot, snapshot, path, identity, uri, connection, result, 輸入失敗, 配置失敗
            raise
        except 版本存取錯誤:
            del self, owner_user_id, endpoint_id, prepared_snapshot, snapshot, path, identity, uri, connection, result, 輸入失敗, 配置失敗
            raise
        except 端點發布輸入錯誤:
            輸入失敗 = True
        except BaseException:
            配置失敗 = True
        if 輸入失敗:
            del self, owner_user_id, endpoint_id, prepared_snapshot, snapshot, path, identity, uri, connection, result, 輸入失敗, 配置失敗
            _拒絕輸入()
        if 配置失敗 or result is None:
            del self, owner_user_id, endpoint_id, prepared_snapshot, snapshot, path, identity, uri, connection, result, 輸入失敗, 配置失敗
            _拒絕配置()
        del self, owner_user_id, endpoint_id, prepared_snapshot, snapshot, path, identity, uri, connection, 輸入失敗, 配置失敗
        return result

def _配置交易(
    connection: sqlite3.Connection, owner: str, endpoint_id: str,
    snapshot: 發布版本快照, id_factory: Callable[[], str], clock: Callable[[], float],
) -> 版本配置結果:
    """鎖後驗 schema/authority/序列，唯一 INSERT 後耐久提交。"""
    begun = committed = ordinary_failure = access_failure = False
    ledger = rows = raw = endpoint = aggregate = previous = version_id = created_at = result = None
    count = minimum = maximum = number = input_json = response_json = parameters = cursor = None
    input_equal = response_equal = changed = None
    rollback_controls: list[BaseException] = []
    close_controls: list[BaseException] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise sqlite3.DatabaseError
        connection.execute("BEGIN IMMEDIATE")
        begun = True
        ledger = tuple(connection.execute("SELECT version,name FROM published_api_schema_migrations ORDER BY version"))
        rows = list(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if ledger != _遷移ledger or hashlib.sha256(raw.encode()).hexdigest() != _schema指紋:
            raise sqlite3.DatabaseError
        endpoint = connection.execute(
            "SELECT owner_user_id,status FROM published_endpoints WHERE id=?", (endpoint_id,),
        ).fetchone()
        access_failure = endpoint is None or endpoint != (owner, "active")
        if not access_failure:
            aggregate = connection.execute(
                "SELECT count(*),min(version_number),max(version_number) FROM published_endpoint_versions WHERE endpoint_id=?",
                (endpoint_id,),
            ).fetchone()
            count, minimum, maximum = aggregate
            if count and (minimum != 1 or maximum != count):
                raise sqlite3.DatabaseError
            number = count + 1
            if count:
                previous = connection.execute(
                    "SELECT input_schema_json,response_schema_json FROM published_endpoint_versions WHERE endpoint_id=? AND version_number=?",
                    (endpoint_id, count),
                ).fetchone()
                if type(previous) is not tuple or len(previous) != 2:
                    raise sqlite3.DatabaseError
            version_id = id_factory()
            created_at = clock()
            if not _是識別(version_id) or not _是有限非負(created_at):
                raise ValueError
            input_json = None if snapshot.input_schema is None else _正規JSON(snapshot.input_schema)
            response_json = _正規JSON(snapshot.response_schema)
            input_equal = _schema等價(previous[0], input_json) if count else True
            response_equal = _schema等價(previous[1], response_json) if count else True
            changed = bool(count) and not (input_equal and response_equal)
            parameters = (
                version_id, endpoint_id, number, snapshot.original_requirement_text, snapshot.system_prompt,
                _正規JSON(snapshot.allowed_skills), _正規JSON(snapshot.allowed_tools),
                _正規JSON(snapshot.tool_schema_snapshot), snapshot.tool_runtime_revision,
                _正規JSON(snapshot.model_config_snapshot), _正規JSON(snapshot.retry_policy),
                _正規JSON(snapshot.skill_bundle_manifest), input_json, response_json, int(changed),
                snapshot.created_by_user_id, created_at,
            )
            cursor = connection.execute("INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", parameters)
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError
            connection.execute("COMMIT")
            begun = False
            committed = True
            result = 版本配置結果(version_id, endpoint_id, number, changed, created_at)
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as control:
        _清除例外鏈(control)
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
        rollback_controls.clear()
        close_controls.clear()
        _清除例外鏈(control)
        del connection, owner, endpoint_id, snapshot, id_factory, clock
        del begun, committed, ordinary_failure, access_failure, ledger, rows, raw, endpoint, aggregate, previous
        del version_id, created_at, result, count, minimum, maximum, number, input_json, response_json
        del parameters, cursor, input_equal, response_equal, changed, rollback_controls, close_controls
        del control
        raise
    except BaseException:
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
        ordinary_failure = True
    if access_failure:
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
    if ordinary_failure or access_failure:
        denied = access_failure and not ordinary_failure
        del connection, owner, endpoint_id, snapshot, id_factory, clock
        del begun, committed, ordinary_failure, access_failure, ledger, rows, raw, endpoint, aggregate, previous
        del version_id, created_at, result, count, minimum, maximum, number, input_json, response_json
        del parameters, cursor, input_equal, response_equal, changed
        if rollback_controls:
            close_controls.clear()
            _拋出清理控制(rollback_controls.pop())
        if close_controls:
            _拋出清理控制(close_controls.pop())
        del rollback_controls, close_controls
        if denied:
            del denied
            raise 版本存取錯誤("版本配置存取遭拒") from None
        del denied
        _拒絕配置()
    close_controls = _安全關閉(connection)
    del connection, owner, endpoint_id, snapshot, id_factory, clock
    del begun, committed, ordinary_failure, access_failure, ledger, rows, raw, endpoint, aggregate, previous
    del version_id, created_at, count, minimum, maximum, number, input_json, response_json
    del parameters, cursor, input_equal, response_equal, changed, rollback_controls
    if close_controls:
        del result
        _拋出清理控制(close_controls.pop())
    del close_controls
    assert type(result) is 版本配置結果
    return result


def _驗證schema(connection: sqlite3.Connection) -> None:
    """在既有交易/read snapshot 驗證完整 migration ledger 與 schema。"""
    ledger = rows = raw = None
    try:
        ledger = tuple(connection.execute(
            "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
        ))
        rows = list(connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ))
        raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        if ledger != _遷移ledger or hashlib.sha256(raw.encode()).hexdigest() != _schema指紋:
            raise sqlite3.DatabaseError
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        if type(rows) is list:
            rows.clear()
        del connection, ledger, rows, raw
        raise
    del connection, ledger, rows, raw


def _解析正規值(text: str) -> Any:
    """有界解析 persisted canonical JSON，拒絕 duplicate/noncanonical。"""
    value = None
    try:
        _驗證JSON文字界限(text)
        value = json.loads(text, object_pairs_hook=_唯一物件, parse_constant=_拒絕JSON常數)
        if _正規JSON(value) != text:
            raise ValueError
        return value
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        if type(value) in (dict, list):
            value.clear()
        del text, value
        raise
    except BaseException:
        if type(value) in (dict, list):
            value.clear()
        del text, value
        raise sqlite3.DatabaseError from None


def _解析正規物件(text: str) -> dict[str, Any]:
    """解析 canonical JSON object，並在控制流穿透前清除完整原文。"""
    value: Any = None
    try:
        value = _解析正規值(text)
        if type(value) is not dict:
            if type(value) is list:
                value.clear()
            raise sqlite3.DatabaseError
        return value
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        if type(value) in (dict, list):
            value.clear()
        del text, value
        raise
    except BaseException:
        if type(value) in (dict, list):
            value.clear()
        del text, value
        raise sqlite3.DatabaseError from None


def _擷取呼叫目標(回呼: Callable[..., Any]) -> Callable[..., Any]:
    """擷取當下 bound call target，避免之後重新 dispatch 可變的類別 descriptor。"""
    描述器 = 目標 = None
    try:
        if isinstance(回呼, type):
            描述器 = getattr(type(回呼), "__call__")
            目標 = 描述器.__get__(回呼, type(回呼))
        else:
            目標 = getattr(回呼, "__call__")
        if not callable(目標):
            raise TypeError
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制)
        del 回呼, 描述器, 目標, 控制
        raise
    except BaseException:
        del 回呼, 描述器, 目標
        raise
    del 回呼, 描述器
    return 目標


def _schema等價(left: str | None, right: str | None) -> bool:
    """比較 JSON schema 語意；數值跨表示法等價且不犧牲 huge-int identity。"""
    if left is None or right is None:
        return left is right
    first = second = result = None
    failed = False
    try:
        _驗證JSON文字界限(left)
        _驗證JSON文字界限(right)
        first = json.loads(
            left, parse_float=Decimal, parse_int=int, object_pairs_hook=_唯一物件,
            parse_constant=_拒絕JSON常數,
        )
        second = json.loads(
            right, parse_float=Decimal, parse_int=int, object_pairs_hook=_唯一物件,
            parse_constant=_拒絕JSON常數,
        )
        result = _JSON等價(first, second, [0], 0)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        if type(first) is list:
            list.clear(first)
        elif type(first) is dict:
            dict.clear(first)
        if type(second) is list:
            list.clear(second)
        elif type(second) is dict:
            dict.clear(second)
        del left, right, first, second, result, failed
        raise
    except (ValueError, TypeError, ArithmeticError, RecursionError):
        if type(first) is list:
            list.clear(first)
        elif type(first) is dict:
            dict.clear(first)
        if type(second) is list:
            list.clear(second)
        elif type(second) is dict:
            dict.clear(second)
        failed = True
    if failed:
        del left, right, first, second, result, failed
        raise sqlite3.DatabaseError from None
    del left, right, first, second, failed
    assert type(result) is bool
    return result


def _拒絕JSON常數(_value: str) -> NoReturn:
    """JSON schema 不接受 NaN 與正負 Infinity。"""
    del _value
    raise ValueError


def _驗證JSON文字界限(value: str) -> None:
    """在 parser 配置容器前限制 UTF-8、巢狀深度與近似節點數。"""
    depth = nodes = 0
    quoted = escaped = False
    character = None
    try:
        if type(value) is not str or len(value.encode("utf-8")) > _JSON文字上限:
            raise ValueError
        for character in value:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
            elif character == '"':
                quoted = True
            elif character in "[{":
                depth += 1
                nodes += 1
                if depth > _JSON深度上限 or nodes > _JSON節點上限:
                    raise ValueError
            elif character in ",:":
                nodes += 1
                if nodes > _JSON節點上限:
                    raise ValueError
            elif character in "]}":
                depth -= 1
                if depth < 0:
                    raise ValueError
        if quoted or escaped or depth != 0:
            raise ValueError
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del value, depth, nodes, quoted, escaped, character
        raise


def _唯一物件(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    key = value = None
    try:
        for key, value in pairs:
            if type(key) is not str or key in result:
                raise ValueError
            result[key] = value
            key = value = None
    except BaseException:
        result.clear()
        pairs.clear()
        del pairs, result, key, value
        raise
    del pairs, key, value
    return result


def _JSON等價(left: Any, right: Any, count: list[int], depth: int) -> bool:
    """以 cleanup-aware 遞迴比較 exact JSON tree，不建立 generator frame。"""
    key = item_left = item_right = None
    result = False
    try:
        count[0] += 1
        if count[0] > _JSON節點上限 or depth > _JSON深度上限:
            raise ValueError
        if type(left) is bool or type(right) is bool:
            result = type(left) is type(right) and left is right
        elif type(left) in (int, Decimal) and type(right) in (int, Decimal):
            result = left == right
        elif type(left) is not type(right):
            result = False
        elif type(left) is list:
            result = len(left) == len(right)
            if result:
                for index in range(len(left)):
                    item_left, item_right = left[index], right[index]
                    if not _JSON等價(item_left, item_right, count, depth + 1):
                        result = False
                        break
                    item_left = item_right = None
        elif type(left) is dict:
            result = left.keys() == right.keys()
            if result:
                for key in left:
                    item_left, item_right = left[key], right[key]
                    if not _JSON等價(item_left, item_right, count, depth + 1):
                        result = False
                        break
                    key = item_left = item_right = None
        else:
            result = left == right
    except BaseException:
        count.clear()
        del left, right, count, depth, key, item_left, item_right, result
        raise
    del left, right, count, depth, key, item_left, item_right
    return result


def _拒絕輸入() -> NoReturn:
    raise 版本配置輸入錯誤("版本配置輸入無效") from None


def _拒絕配置() -> NoReturn:
    raise 版本配置錯誤("版本配置失敗") from None
