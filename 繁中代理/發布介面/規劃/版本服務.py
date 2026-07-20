"""PUB P05 既有發布端點的不可變版本配置服務。

參數／欄位：不適用；本模組定義版本配置、啟用與目前版本解析契約。
回傳：不適用；各服務與資料型別的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、常數與函式，不開啟資料庫或變更目前版本。
"""

from __future__ import annotations

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
    _正規JSON,
    _是有限非負,
    _是識別,
    _安全回滾,
    _安全關閉,
    _拋出清理控制,
    _清除例外鏈,
    _重建版本快照,
    _驗證已開啟資料庫路徑,
    _驗證既有資料庫路徑,
)
from ..技能套件.發布器 import (
    套件發布收據,
    已驗證技能套件清單,
)
from ..資料庫結構契約 import 驗證資料庫結構 as _權威驗證資料庫結構
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
    """描述 external verifier 只能接收 detached authoritative projection。

    參數：實作者接收不可變清單投影、版本識別碼與端點識別碼。
    回傳：只有 exact candidate 已發布時回傳 exact ``True``。
    例外：實作者可傳出驗證失敗；服務會保留控制流程例外並回滾普通失敗。
    副作用：契約要求唯讀且冪等，不接收路徑或開啟的描述元 authority。
    """

    def __call__(
        self, manifest: 已驗證技能套件清單, version_id: str, endpoint_id: str,
    ) -> bool:
        """驗證同一 descriptor authority 建立的脫離清單投影。

        參數：清單投影及 prepared 版本、端點識別碼共同限定 exact candidate。
        回傳：驗證成功回傳 exact ``True``，其餘布林值皆不授權提交。
        例外：實作者例外原樣離開 callback boundary，再由服務依固定契約處理。
        副作用：只允許唯讀、冪等驗證；不得藉路徑重新取得套件內容 authority。
        """
        ...


@dataclass(frozen=True, slots=True)
class 版本啟用結果:
    """保存成功提交後的不可變目前版本指標與稽核收據。

    欄位：``endpoint_id``、舊版與新版識別碼標示指標變更；``version_number``、
    ``audit_id`` 與 ``activated_at`` 保存版本號碼、稽核識別碼及啟用時間。
    回傳：建立通過欄位驗證的不可變啟用結果。
    例外：欄位型別、格式或界限無效時拋出 ``版本啟用輸入錯誤``。
    副作用：建構時只驗證並保存欄位，不讀寫資料庫或稽核儲存區。
    """

    endpoint_id: str
    old_version_id: str | None
    new_version_id: str
    version_number: int
    audit_id: str
    activated_at: float

    def __post_init__(self) -> None:
        """驗證版本啟用結果的識別碼、版本號碼與時間。

        參數：無額外參數；讀取目前結果實例的六個不可變欄位。
        回傳：驗證成功時回傳 ``None``。
        例外：欄位 exact type、格式或界限不符時拋出 ``版本啟用輸入錯誤``。
        副作用：只讀取實例欄位，不修改資料庫或外部狀態。
        """
        if (type(self) is not 版本啟用結果 or not _是識別(self.endpoint_id)
                or (self.old_version_id is not None and not _是識別(self.old_version_id))
                or not _是識別(self.new_version_id) or not _是識別(self.audit_id)
                or type(self.version_number) is not int or self.version_number <= 0
                or not _是有限非負(self.activated_at)):
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None


@dataclass(frozen=True, slots=True)
class 已釘選版本:
    """只保存不可變純量與正規文字，每次取用皆重建脫離快照。

    欄位：端點、服務帳戶與版本識別碼指定版本；版本號碼、結構變更旗標、建立時間
    與正規版本 JSON 保存可重驗內容。
    回傳：建立可反覆重建全新 ``發布版本快照`` 的不可變釘選版本。
    例外：欄位或正規內容無法安全重建時拋出 ``目前版本解析錯誤``；控制流程例外原樣傳出。
    副作用：建構時解析並重建短暫快照以驗證內容，不讀寫資料庫或修改外部狀態。
    """

    endpoint_id: str
    service_account_id: str
    version_id: str
    version_number: int
    schema_changed: bool
    created_at: float
    _版本JSON: str = field(repr=False)

    def __post_init__(self) -> None:
        """重驗釘選版本純量並確認正規版本快照可安全重建。

        參數：無額外參數；讀取目前實例的釘選欄位與正規 JSON 文字。
        回傳：驗證成功時回傳 ``None``。
        例外：控制流程例外原樣傳出；其他資料或解析異常固定映射為
        ``目前版本解析錯誤``。
        副作用：配置並清除短暫快照資料，不讀寫資料庫或修改外部狀態。
        """
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
    """以立即交易配置下一個只能建立一次的不可變版本。

    參數／欄位：建構時保存資料庫路徑、版本識別工廠、時鐘與連線工廠。
    回傳：建立可執行配置、啟用及目前版本解析的服務實例。
    例外：建構只保存參照；個別服務操作依其文件字串傳出輸入、存取或交易錯誤。
    副作用：建構不呼叫工廠或開啟資料庫；服務操作才會依各自契約管理交易。
    """

    def __init__(
        self,
        database_path: str | Path,
        version_id_factory: Callable[[], str],
        clock: Callable[[], float],
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        """保存版本配置服務所需路徑、工廠與時鐘。

        參數：資料庫路徑指定既有 SQLite；版本識別工廠與時鐘提供交易資料；
        連線工廠建立受服務管理的連線。
        回傳：無。
        例外：建構只保存參照，沒有預期例外。
        副作用：不呼叫任何工廠、不開啟資料庫，也不開始交易。
        """
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

    def 啟用(
        self, owner_user_id: str, endpoint_id: str, version_id: str, *,
        request_id: str | None = None, bundle_verifier: BundlePublicationVerifier,
        audit_id_factory: Callable[[], str], clock: Callable[[], float],
    ) -> 版本啟用結果:
        """鎖後授權、驗 bundle，並原子寫入 current pointer 與 audit。"""
        path = identity = uri = connection = result = None
        驗證目標 = 稽核目標 = 時鐘目標 = None
        invalid = failed = False
        try:
            invalid = (not _是識別(owner_user_id) or not _是識別(endpoint_id)
                       or not _是識別(version_id)
                       or (request_id is not None and not _是識別(request_id))
                       or not callable(bundle_verifier) or not callable(audit_id_factory)
                       or not callable(clock))
            if not invalid:
                驗證目標 = _擷取呼叫目標(bundle_verifier)
                稽核目標 = _擷取呼叫目標(audit_id_factory)
                時鐘目標 = _擷取呼叫目標(clock)
                path, identity = _驗證既有資料庫路徑(self._資料庫路徑)
                uri = path.as_uri() + "?mode=rw"
                connection = self._連線工廠(uri, uri=True, timeout=30.0, isolation_level=None)
                _驗證已開啟資料庫路徑(connection, path, identity)
                result = _啟用交易(
                    connection, owner_user_id, endpoint_id, version_id, request_id,
                    驗證目標, 稽核目標, 時鐘目標,
                )
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
            _清除例外鏈(控制)
            del self, owner_user_id, endpoint_id, version_id, request_id, bundle_verifier, audit_id_factory, clock
            del path, identity, uri, connection, result, 驗證目標, 稽核目標, 時鐘目標, invalid, failed, 控制
            raise
        except 版本啟用存取錯誤:
            del self, owner_user_id, endpoint_id, version_id, request_id, bundle_verifier, audit_id_factory, clock
            del path, identity, uri, connection, result, 驗證目標, 稽核目標, 時鐘目標, invalid, failed
            raise
        except BaseException:
            failed = True
        if invalid:
            del self, owner_user_id, endpoint_id, version_id, request_id, bundle_verifier, audit_id_factory, clock
            del path, identity, uri, connection, result, 驗證目標, 稽核目標, 時鐘目標, invalid, failed
            raise 版本啟用輸入錯誤("版本啟用輸入無效") from None
        if failed or result is None:
            del self, owner_user_id, endpoint_id, version_id, request_id, bundle_verifier, audit_id_factory, clock
            del path, identity, uri, connection, result, 驗證目標, 稽核目標, 時鐘目標, invalid, failed
            raise 版本啟用錯誤("版本啟用失敗") from None
        del self, owner_user_id, endpoint_id, version_id, request_id, bundle_verifier, audit_id_factory, clock
        del path, identity, uri, connection, 驗證目標, 稽核目標, 時鐘目標, invalid, failed
        return result


class SQLite目前版本解析器:
    """以單一 authoritative JOIN 為 invocation 釘住 current immutable version。"""

    def __init__(
        self, database_path: str | Path,
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        self._資料庫路徑 = database_path
        self._連線工廠 = connection_factory

    def 依slug解析(self, slug: str) -> 已釘選版本:
        """在單一 deferred read transaction 釘住 schema、current JOIN 與 detached 結果。"""
        path = identity = uri = connection = row = payload = result = None
        failed = 不存在 = owned = begun = 已完成 = False
        rollback_controls: list[BaseException] = []
        close_controls: list[BaseException] = []
        try:
            if type(slug) is not str or _slug格式.fullmatch(slug) is None:
                raise ValueError
            path, identity = _驗證既有資料庫路徑(self._資料庫路徑)
            uri = path.as_uri() + "?mode=ro"
            connection = self._連線工廠(uri, uri=True, timeout=30.0, isolation_level=None)
            _驗證已開啟資料庫路徑(connection, path, identity)
            owned = True
            connection.execute("BEGIN")
            begun = True
            _驗證schema(connection)
            row = connection.execute(
                "SELECT e.id,e.service_account_id,e.status,v.id,v.version_number,v.original_requirement_text,v.system_prompt,v.allowed_skills_json,v.allowed_tools_json,v.tool_schema_snapshot_json,v.tool_runtime_revision,v.model_config_snapshot_json,v.retry_policy_json,v.skill_bundle_manifest_json,v.input_schema_json,v.response_schema_json,v.schema_changed,v.created_by_user_id,v.created_at FROM published_endpoints e JOIN published_endpoint_versions v ON v.id=e.current_version_id AND v.endpoint_id=e.id WHERE e.slug=? AND e.status='active' AND v.id=e.current_version_id AND v.endpoint_id=e.id",
                (slug,),
            ).fetchone()
            if row is None:
                不存在 = True
                raise LookupError
            if (type(row) is not tuple or len(row) != 19 or row[2] != "active"
                    or not _是識別(row[0]) or not _是識別(row[1]) or not _是識別(row[3])
                    or type(row[4]) is not int or row[4] <= 0
                    or type(row[16]) is not int or row[16] not in (0, 1)
                    or not _是有限非負(row[18])):
                raise sqlite3.DatabaseError
            payload = {
                "original_requirement_text": row[5], "system_prompt": row[6],
                "allowed_skills": _解析正規值(row[7]), "allowed_tools": _解析正規值(row[8]),
                "tool_schema_snapshot": _解析正規值(row[9]), "tool_runtime_revision": row[10],
                "model_config_snapshot": _解析正規值(row[11]), "retry_policy": _解析正規值(row[12]),
                "skill_bundle_manifest": _解析正規值(row[13]),
                "input_schema": None if row[14] is None else _解析正規值(row[14]),
                "response_schema": _解析正規值(row[15]), "created_by_user_id": row[17],
            }
            result = 已釘選版本(
                row[0], row[1], row[3], row[4], row[16] == 1, row[18], _正規JSON(payload),
            )
            payload.clear()
            connection.execute("COMMIT")
            begun = False
            已完成 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as control:
            _清除例外鏈(control)
            if begun:
                rollback_controls = _安全回滾(connection)
            if owned:
                close_controls = _安全關閉(connection)
            rollback_controls.clear(); close_controls.clear(); _清除例外鏈(control)
            if type(payload) is dict:
                payload.clear()
            del self, slug, path, identity, uri, connection, row, payload, result
            del failed, 不存在, owned, begun, 已完成, rollback_controls, close_controls, control
            raise
        except BaseException:
            failed = True
        if failed or result is None or not 已完成:
            if begun:
                rollback_controls = _安全回滾(connection)
                begun = False
            if owned:
                close_controls = _安全關閉(connection)
            if type(payload) is dict:
                payload.clear()
            應拋不存在 = 不存在
            del self, slug, path, identity, uri, connection, row, payload
            del 不存在, owned, begun, 已完成
            if rollback_controls:
                close_controls.clear(); del result, failed, 應拋不存在
                _拋出清理控制(rollback_controls.pop())
            if close_controls:
                del result, failed, 應拋不存在, rollback_controls
                _拋出清理控制(close_controls.pop())
            del result, failed, rollback_controls, close_controls
            if 應拋不存在:
                del 應拋不存在
                raise 目前版本不存在錯誤("目前版本不存在") from None
            del 應拋不存在
            raise 目前版本解析錯誤("目前版本解析失敗") from None
        close_controls = _安全關閉(connection)
        if type(payload) is dict:
            payload.clear()
        del self, slug, path, identity, uri, connection, row, payload
        del failed, 不存在, owned, begun, 已完成, rollback_controls
        if close_controls:
            del result
            _拋出清理控制(close_controls.pop())
        del close_controls
        return result

def _重建原子套件收據(快照: 發布版本快照, 來源: 套件發布收據) -> 套件發布收據:
    """一次讀取 hostile receipt slots，並與 prepared snapshot 的固定投影比對。

    參數：``快照`` 是服務持有副本；``來源`` 是仍不可信的發布收據。
    回傳：欄位與快照完全一致的新 ``套件發布收據``。
    例外：控制流程例外原樣傳出；任何欄位或關係失敗映射為固定輸入錯誤。
    副作用：只配置脫離收據並比較純量，不開啟資料庫或存取檔案系統。
    """
    快照副本, 來源副本 = 快照, 來源
    try:
        if type(來源副本) is not 套件發布收據:
            raise ValueError
        欄位值 = tuple(
            object.__getattribute__(來源副本, 欄位名)
            for 欄位名 in 套件發布收據.__dataclass_fields__
        )
        收據副本 = 套件發布收據(*欄位值)
        清單 = 快照副本.skill_bundle_manifest
        if (
            type(清單) is not dict or 收據副本.套件識別碼 != 清單.get("bundle_id")
            or 收據副本.清單參照 != 清單.get("manifest_reference")
            or 收據副本.清單摘要 != 清單.get("manifest_digest")
            or 收據副本.套件雜湊 != 清單.get("sha256")
            or type(收據副本.路徑) is not type(Path()) or 收據副本.路徑.name != 收據副本.套件識別碼
            or type(收據副本.總位元組數) is not int or not 0 <= 收據副本.總位元組數 <= 4 * 1024 * 1024
        ):
            raise ValueError
        return 收據副本
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise 端點發布輸入錯誤("端點發布輸入無效") from None


def _交易仍在進行(連線: sqlite3.Connection) -> bool:
    """以 SQLite base descriptor 讀取一次權威交易狀態。

    參數：``連線`` 必須是 ``sqlite3.Connection`` 或其子類別。
    回傳：base descriptor 的 exact bool，表示目前是否仍有可回滾交易。
    例外：非 SQLite connection、descriptor 失敗或非 bool 結果時拋出 ``TypeError``。
    副作用：只讀一次 base ``in_transaction``，不觸發子類別同名 override。
    """
    if not isinstance(連線, sqlite3.Connection):
        raise TypeError("必須使用 sqlite3.Connection")
    狀態 = sqlite3.Connection.in_transaction.__get__(連線, sqlite3.Connection)
    if type(狀態) is not bool:
        raise TypeError("SQLite 交易狀態無效")
    return 狀態


def _符合原子配置提交後條件(
    連線: sqlite3.Connection, 結果: 版本配置結果, 快照: 發布版本快照,
    收據: 套件發布收據, 稽核: str, 請求: str | None, 執行者類型: str,
    執行者: str, 舊版本: str, 套件摘要: str,
) -> bool:
    """COMMIT acknowledgement 遺失時證明四個 exact 耐久投影。

    參數：連線與 prepared 結果、快照、收據、稽核及 actor 純量描述唯一預期狀態。
    回傳：版本列、bundle 收據、audit 與 current pointer 全部 exact 相符時為真。
    例外：控制流程例外原樣傳出；查詢或比較的一般失敗安全轉為 ``False``。
    副作用：以 ``sqlite3.Connection.execute`` 唯讀查詢四個已耐久投影。
    """
    try:
        版本列 = sqlite3.Connection.execute(
            連線,
            "SELECT id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,created_by_user_id,created_at FROM published_endpoint_versions WHERE id=?",
            (結果.version_id,),
        ).fetchone()
        收據列 = sqlite3.Connection.execute(
            連線,
            "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at FROM published_skill_bundles WHERE version_id=?",
            (結果.version_id,),
        ).fetchone()
        稽核列 = sqlite3.Connection.execute(
            連線,
            "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at FROM audit_events WHERE id=?",
            (稽核,),
        ).fetchone()
        指標列 = sqlite3.Connection.execute(
            連線, "SELECT current_version_id,updated_at FROM published_endpoints WHERE id=?",
            (結果.endpoint_id,),
        ).fetchone()
        預期版本 = (
            結果.version_id, 結果.endpoint_id, 結果.version_number,
            快照.original_requirement_text, 快照.system_prompt,
            _正規JSON(快照.allowed_skills), _正規JSON(快照.allowed_tools),
            _正規JSON(快照.tool_schema_snapshot), 快照.tool_runtime_revision,
            _正規JSON(快照.model_config_snapshot), _正規JSON(快照.retry_policy),
            _正規JSON(快照.skill_bundle_manifest),
            None if 快照.input_schema is None else _正規JSON(快照.input_schema),
            _正規JSON(快照.response_schema), int(結果.schema_changed),
            快照.created_by_user_id, 結果.created_at,
        )
        預期收據 = (
            收據.套件識別碼, 結果.version_id, 收據.清單參照, 收據.清單摘要,
            收據.套件雜湊, 收據.總位元組數, "published", 結果.created_at, None,
        )
        中繼資料 = _正規JSON({
            "old_version_id": 舊版本, "new_version_id": 結果.version_id,
            "version_number": 結果.version_number, "bundle_sha256": 套件摘要,
        })
        預期稽核 = (
            稽核, 稽核, 結果.created_at, "endpoint_version_activated", "success",
            執行者類型, 執行者, "published_endpoint_version", 結果.version_id,
            請求, 結果.endpoint_id, None, 中繼資料, 結果.created_at,
        )
        return (
            版本列 == 預期版本 and 收據列 == 預期收據 and 稽核列 == 預期稽核
            and 指標列 == (結果.version_id, 結果.created_at)
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        return False


def _確保回滾(連線: sqlite3.Connection) -> list[BaseException]:
    """完成 owned 交易回滾並收集清理控制例外。

    參數：``連線`` 是服務擁有且可能仍在交易中的 SQLite connection。
    回傳：依發生順序保存 rollback 控制流程例外的 list。
    例外：一般 rollback、狀態讀取與 base fallback 失敗皆抑制。
    副作用：先呼叫一般 ``ROLLBACK`` 一次，必要時再以 base rollback 釋放交易。
    """
    控制列: list[BaseException] = []
    try:
        連線.execute("ROLLBACK")
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制); 控制.__traceback__ = None; 控制列.append(控制)
    except BaseException:
        pass
    try:
        if _交易仍在進行(連線):
            sqlite3.Connection.rollback(連線)
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制); 控制.__traceback__ = None; 控制列.append(控制)
    except BaseException:
        pass
    return 控制列


def _確保關閉(連線: sqlite3.Connection) -> list[BaseException]:
    """完成 owned connection 關閉並收集清理控制例外。

    參數：``連線`` 是服務擁有且不再供交易主體使用的 SQLite connection。
    回傳：依發生順序保存 close 控制流程例外的 list。
    例外：一般 close 與 base fallback 的普通失敗皆抑制。
    副作用：先呼叫可覆寫 close 一次，失敗時再以 base close 釋放 handle。
    """
    控制列: list[BaseException] = []
    需繞過 = False
    try:
        連線.close()
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制); 控制.__traceback__ = None; 控制列.append(控制); 需繞過 = True
    except BaseException:
        需繞過 = True
    if 需繞過:
        try:
            sqlite3.Connection.close(連線)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
            _清除例外鏈(控制); 控制.__traceback__ = None; 控制列.append(控制)
        except BaseException:
            pass
    return 控制列


def _配置交易(
    connection: sqlite3.Connection, owner: str, endpoint_id: str,
    snapshot: 發布版本快照, id_factory: Callable[[], str], clock: Callable[[], float],
) -> 版本配置結果:
    """鎖後驗證結構、權限與序列，唯一寫入後耐久提交。

    參數：連線由本函式完成交易及關閉；擁有者與端點識別授權目標；快照提供
    不可變版本資料；識別工廠與時鐘提供新列純量。
    回傳：成功提交後的 ``版本配置結果``。
    例外：存取不符時拋出 ``版本存取錯誤``；一般交易失敗固定映射為
    ``版本配置錯誤``；清理控制流程例外依契約原樣傳出。
    副作用：設定外鍵、開始立即交易、驗證資料庫、插入版本、提交並關閉連線；
    失敗時回滾且關閉連線。
    """
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
        _權威驗證資料庫結構(connection)
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
    """在既有交易讀取快照內驗證完整遷移帳本與資料庫結構。

    參數：``connection`` 是已開始交易且由呼叫端管理的 SQLite 連線。
    回傳：中央帳本與結構指紋完全符合時回傳 ``None``。
    例外：控制流程例外原樣傳出；中央驗證器的資料庫結構錯誤原樣傳出。
    副作用：只讀取中央結構中繼資料並清除短暫容器，不提交、回滾或關閉連線。
    """
    ledger = rows = raw = None
    try:
        _權威驗證資料庫結構(connection)
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


def _啟用交易(
    connection: sqlite3.Connection, owner: str, endpoint_id: str, version_id: str,
    request_id: str | None, 驗證目標: Callable[..., Any],
    稽核目標: Callable[[], str], 時鐘目標: Callable[[], float],
) -> 版本啟用結果:
    """同一 BEGIN IMMEDIATE 內驗證 bundle、append audit 並 CAS pointer。"""
    begun = access_failure = ordinary_failure = False
    endpoint = candidate = aggregate = current = manifest = current_number = None
    old_id = audit_id = activated_at = metadata = result = cursor = proof = sha256 = None
    rollback_controls: list[BaseException] = []
    close_controls: list[BaseException] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise sqlite3.DatabaseError
        connection.execute("BEGIN IMMEDIATE")
        begun = True
        _驗證schema(connection)
        endpoint = connection.execute(
            "SELECT owner_user_id,status,current_version_id FROM published_endpoints WHERE id=?",
            (endpoint_id,),
        ).fetchone()
        if endpoint is None:
            access_failure = True
        elif (type(endpoint) is not tuple or len(endpoint) != 3
              or (endpoint[2] is not None and not _是識別(endpoint[2]))):
            raise sqlite3.DatabaseError
        else:
            access_failure = endpoint[:2] != (owner, "active")
        if not access_failure:
            old_id = endpoint[2]
            candidate = connection.execute(
                "SELECT version_number,skill_bundle_manifest_json FROM published_endpoint_versions WHERE id=? AND endpoint_id=?",
                (version_id, endpoint_id),
            ).fetchone()
            aggregate = connection.execute(
                "SELECT count(*),min(version_number),max(version_number) FROM published_endpoint_versions WHERE endpoint_id=?",
                (endpoint_id,),
            ).fetchone()
            if (type(candidate) is not tuple or len(candidate) != 2
                    or type(candidate[0]) is not int or candidate[0] <= 0
                    or type(candidate[1]) is not str
                    or type(aggregate) is not tuple or len(aggregate) != 3
                    or type(aggregate[0]) is not int or type(aggregate[1]) is not int
                    or type(aggregate[2]) is not int
                    or aggregate[0] <= 0 or aggregate[1] != 1
                    or aggregate[2] != aggregate[0]):
                raise sqlite3.DatabaseError
            current_number = 0
            if old_id is not None:
                current = connection.execute(
                    "SELECT version_number FROM published_endpoint_versions WHERE id=? AND endpoint_id=?",
                    (old_id, endpoint_id),
                ).fetchone()
                if (type(current) is not tuple or len(current) != 1
                        or type(current[0]) is not int or current[0] <= 0):
                    raise sqlite3.DatabaseError
                current_number = current[0]
            if candidate[0] != current_number + 1:
                raise sqlite3.DatabaseError
            manifest = _解析正規物件(candidate[1])
            sha256 = manifest.get("sha256")
            if type(sha256) is not str or len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
                raise sqlite3.DatabaseError
            proof = 驗證目標(manifest, version_id, endpoint_id)
            if type(proof) is not bool or not proof:
                raise sqlite3.DatabaseError
            manifest = None
            proof = None
            audit_id, activated_at = 稽核目標(), 時鐘目標()
            if not _是識別(audit_id) or not _是有限非負(activated_at):
                raise ValueError
            metadata = _正規JSON({
                "old_version_id": old_id, "new_version_id": version_id,
                "version_number": candidate[0], "bundle_sha256": sha256,
            })
            cursor = connection.execute(
                "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) VALUES(?,?,?,'endpoint_version_activated','success','user',?,'published_endpoint_version',?,?,?,NULL,?,?)",
                (audit_id, audit_id, activated_at, owner, version_id, request_id,
                 endpoint_id, metadata, activated_at),
            )
            if type(cursor.rowcount) is not int or cursor.rowcount != 1:
                raise sqlite3.DatabaseError
            cursor = connection.execute(
                "UPDATE published_endpoints SET current_version_id=?,updated_at=? WHERE id=? AND owner_user_id=? AND status='active' AND current_version_id IS ?",
                (version_id, activated_at, endpoint_id, owner, old_id),
            )
            if type(cursor.rowcount) is not int or cursor.rowcount != 1:
                raise sqlite3.DatabaseError
            connection.execute("COMMIT")
            begun = False
            result = 版本啟用結果(endpoint_id, old_id, version_id, candidate[0], audit_id, activated_at)
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as control:
        _清除例外鏈(control)
        manifest = None
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
        rollback_controls.clear(); close_controls.clear()
        _清除例外鏈(control)
        del connection, owner, endpoint_id, version_id, request_id, 驗證目標, 稽核目標, 時鐘目標
        del begun, access_failure, ordinary_failure, endpoint, candidate, aggregate, current, manifest, current_number
        del old_id, audit_id, activated_at, metadata, result, cursor, proof, sha256, rollback_controls, close_controls, control
        raise
    except BaseException:
        manifest = None
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
        ordinary_failure = True
    if access_failure and not ordinary_failure:
        if begun:
            rollback_controls = _安全回滾(connection)
        close_controls = _安全關閉(connection)
    if ordinary_failure or access_failure:
        denied = access_failure and not ordinary_failure
        del connection, owner, endpoint_id, version_id, request_id, 驗證目標, 稽核目標, 時鐘目標
        del begun, access_failure, ordinary_failure, endpoint, candidate, aggregate, current, manifest, current_number
        del old_id, audit_id, activated_at, metadata, result, cursor, proof, sha256
        if rollback_controls:
            close_controls.clear(); _拋出清理控制(rollback_controls.pop())
        if close_controls:
            _拋出清理控制(close_controls.pop())
        del rollback_controls, close_controls
        if denied:
            del denied
            raise 版本啟用存取錯誤("版本啟用存取遭拒") from None
        del denied
        raise 版本啟用錯誤("版本啟用失敗") from None
    close_controls = _安全關閉(connection)
    del connection, owner, endpoint_id, version_id, request_id, 驗證目標, 稽核目標, 時鐘目標
    del begun, access_failure, ordinary_failure, endpoint, candidate, aggregate, current, manifest, current_number
    del old_id, audit_id, activated_at, metadata, cursor, proof, sha256, rollback_controls
    if close_controls:
        del result
        _拋出清理控制(close_controls.pop())
    del close_controls
    assert type(result) is 版本啟用結果
    return result


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
