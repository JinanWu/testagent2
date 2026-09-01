"""Endpoint credential安全建立與SQLite持久化。"""

from __future__ import annotations

import inspect
import ipaddress
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..領域模型 import AuditMetadata, WebOwnerPrincipal
from ..憑證管理契約 import (
    找不到端點憑證錯誤, 憑證管理操作錯誤, 憑證管理錯誤, 端點生命週期衝突錯誤,
)
from .加密 import AESGCM憑證封套, 新APIKey

_未提供 = object()
_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _清除例外鏈(錯誤: BaseException) -> None:
    """移除控制例外既有鏈，避免cleanup重掛敏感primary。"""
    BaseException.__setattr__(錯誤, "__cause__", None)
    BaseException.__setattr__(錯誤, "__context__", None)
    BaseException.__setattr__(錯誤, "__suppress_context__", True)


def _重新拋出控制(錯誤: BaseException) -> None:
    """以空caller box保留exact控制identity。"""
    _清除例外鏈(錯誤)
    try:
        raise 錯誤
    except _控制例外:
        del 錯誤
        raise


class 憑證儲存錯誤(ValueError):
    """不暴露tenant、secret或SQLite internals的固定legacy錯誤。"""


@dataclass(frozen=True, slots=True)
class 建立憑證結果:
    """Create-only plaintext response；list/detail不得重用此DTO。"""

    credential_id: str
    api_key: str = field(repr=False)
    name: str
    purpose: str
    key_prefix: str
    key_last4: str
    expires_at: float
    ip_allowlist: tuple[str, ...]
    rate_limit_requests: int
    __annotations__["created_at"] = "float"


class SQLite憑證儲存庫:
    """以單一BEGIN IMMEDIATE transaction建立owner-bound credential。"""

    def __init__(
        self,
        database: str | Path,
        envelope: AESGCM憑證封套,
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if type(envelope) is not AESGCM憑證封套 or not callable(clock):
            del database, envelope, clock, id_factory
            raise 憑證儲存錯誤("憑證建立失敗") from None
        try:
            self._database = Path(database)
            self._envelope = envelope
            self._clock = clock
            self._id_factory = id_factory or (lambda: f"cred-{secrets.token_hex(16)}")
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            self.__dict__.clear()
            del database, envelope, clock, id_factory, self
            raise
        except BaseException:
            self.__dict__.clear()
            del database, envelope, clock, id_factory, self
            raise 憑證儲存錯誤("憑證建立失敗") from None

    def 建立(
        self,
        endpoint_id: str,
        actor: WebOwnerPrincipal,
        *,
        name: str,
        purpose: str,
        expires_at: float,
        ip_allowlist: tuple[str, ...] = (),
        rate_limit_requests: int = 60,
    ) -> 建立憑證結果:
        """以歷史單一ValueError taxonomy建立憑證。"""
        結果 = None
        是否失敗 = False
        try:
            結果 = self._建立交易(
                endpoint_id, actor, 名稱=name, 用途=purpose, 到期時間=expires_at,
                IP允許清單=ip_allowlist, 速率限制請求數=rate_limit_requests,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            結果 = None
            del self, endpoint_id, actor, name, purpose, expires_at, ip_allowlist, rate_limit_requests
            raise
        except 憑證管理錯誤:
            是否失敗 = True
        except BaseException:
            是否失敗 = True
        del self, endpoint_id, actor, name, purpose, expires_at, ip_allowlist, rate_limit_requests
        if 是否失敗 or 結果 is None:
            結果 = None
            raise 憑證儲存錯誤("憑證建立失敗") from None
        return 結果

    def 建立管理憑證(
        self, *位置參數, **關鍵字參數,
    ) -> 建立憑證結果:
        """以管理介面typed taxonomy執行同一建立交易。"""
        端點識別碼 = 行動者 = 名稱 = 用途 = 到期時間 = _未提供
        IP允許清單 = 速率限制請求數 = 結果 = _未提供
        try:
            if len(位置參數) > 2:
                raise TypeError("建立管理憑證最多接受兩個位置參數")
            if 位置參數 and "endpoint_id" in 關鍵字參數:
                raise TypeError("endpoint_id 重複提供")
            if len(位置參數) > 1 and "actor" in 關鍵字參數:
                raise TypeError("actor 重複提供")
            端點識別碼 = 位置參數[0] if 位置參數 else 關鍵字參數.pop("endpoint_id", _未提供)
            行動者 = 位置參數[1] if len(位置參數) > 1 else 關鍵字參數.pop("actor", _未提供)
            名稱 = 關鍵字參數.pop("name", _未提供)
            用途 = 關鍵字參數.pop("purpose", _未提供)
            到期時間 = 關鍵字參數.pop("expires_at", _未提供)
            IP允許清單 = 關鍵字參數.pop("ip_allowlist", ())
            速率限制請求數 = 關鍵字參數.pop("rate_limit_requests", 60)
            if (
                關鍵字參數
                or 端點識別碼 is _未提供 or 行動者 is _未提供
                or 名稱 is _未提供 or 用途 is _未提供 or 到期時間 is _未提供
            ):
                raise TypeError("建立管理憑證參數不完整或含未知名稱")
            結果 = self._建立交易(
                端點識別碼, 行動者, 名稱=名稱, 用途=用途, 到期時間=到期時間,
                IP允許清單=IP允許清單, 速率限制請求數=速率限制請求數,
            )
            return 結果
        finally:
            位置參數 = ()
            關鍵字參數.clear()
            端點識別碼 = 行動者 = 名稱 = 用途 = 到期時間 = None
            IP允許清單 = 速率限制請求數 = 結果 = None
            del self

    def _建立交易(
        self, 端點識別碼: str, 行動者: WebOwnerPrincipal, *, 名稱: str, 用途: str,
        到期時間: float, IP允許清單: tuple[str, ...], 速率限制請求數: int,
    ) -> 建立憑證結果:
        """驗owner與輸入後加密、insert、commit；明文只出現在成功回傳。"""
        資料庫 = 時鐘 = 識別碼工廠 = 加密 = None
        現在時間 = 正規化允許清單 = 資料庫URI = 連線 = 擁有者列 = None
        憑證識別碼 = issued = 允許清單JSON = result = None
        committed = False
        失敗類型 = None
        主要控制: list[BaseException] = []
        回滾控制: list[BaseException] = []
        關閉控制: list[BaseException] = []
        try:
            資料庫, 時鐘 = self._database, self._clock
            識別碼工廠, 加密 = self._id_factory, self._envelope.產生並加密
            del self
            現在時間 = float(時鐘())
            正規化允許清單 = SQLite憑證儲存庫._正規化allowlist(IP允許清單)
            SQLite憑證儲存庫._驗證輸入(
                端點識別碼, 行動者, 名稱, 用途, 現在時間, 到期時間, 速率限制請求數,
            )
            資料庫URI = 資料庫.resolve().as_uri() + "?mode=rw"
            連線 = sqlite3.connect(
                資料庫URI, timeout=30, isolation_level=None, uri=True,
            )
            連線.execute("PRAGMA foreign_keys=ON")
            註冊憑證SQLite函式(連線)
            連線.execute("BEGIN IMMEDIATE")
            擁有者列 = 連線.execute(
                "SELECT status FROM published_endpoints WHERE id=? AND owner_user_id=?",
                (端點識別碼, 行動者.user_id),
            ).fetchone()
            if 擁有者列 is None:
                raise 找不到端點憑證錯誤("找不到端點或憑證")
            if type(擁有者列) is not tuple or len(擁有者列) != 1 or 擁有者列[0] not in ("active", "disabled", "archived"):
                raise 憑證管理操作錯誤("憑證管理失敗")
            if 擁有者列[0] != "active":
                raise 端點生命週期衝突錯誤("端點生命週期衝突")
            憑證識別碼 = 識別碼工廠()
            if type(憑證識別碼) is not str:
                raise 憑證管理操作錯誤("憑證建立失敗")
            issued = 加密(端點識別碼, 憑證識別碼)
            允許清單JSON = json.dumps(正規化允許清單, ensure_ascii=True, separators=(",", ":"))
            連線.execute(
                "INSERT INTO endpoint_credentials("
                "id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,"
                "expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,"
                "rate_limit_requests,created_by_user_id,revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,0)",
                (
                    憑證識別碼, 端點識別碼, 名稱, 用途, issued.envelope.key_version,
                    issued.envelope.nonce, issued.envelope.ciphertext, issued.key_hash,
                    issued.key_prefix, issued.key_last4, float(到期時間), None, 現在時間, 現在時間,
                    允許清單JSON, 速率限制請求數, 行動者.user_id,
                ),
            )
            result = 建立憑證結果(
                憑證識別碼, issued.api_key, 名稱, 用途, issued.key_prefix, issued.key_last4,
                float(到期時間), 正規化允許清單, 速率限制請求數, 現在時間,
            )
            連線.execute("COMMIT")
            committed = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            _清除例外鏈(錯誤)
            主要控制.append(錯誤)
        except (找不到端點憑證錯誤, 端點生命週期衝突錯誤, 憑證管理操作錯誤) as 錯誤:
            失敗類型 = type(錯誤)
        except BaseException:
            失敗類型 = 憑證管理操作錯誤
        if not committed and 連線 is not None:
            try:
                if 連線.in_transaction:
                    連線.execute("ROLLBACK")
            except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
                _清除例外鏈(錯誤)
                回滾控制.append(錯誤)
            except BaseException:
                失敗類型 = 憑證管理操作錯誤
        if 連線 is not None:
            待關閉連線, 連線 = 連線, None
            try:
                待關閉連線.close()
            except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
                _清除例外鏈(錯誤)
                關閉控制.append(錯誤)
            except BaseException:
                if not committed:
                    失敗類型 = 憑證管理操作錯誤
            待關閉連線 = None
        if 主要控制:
            回滾控制.clear()
            關閉控制.clear()
        elif 回滾控制:
            關閉控制.clear()
        elif 關閉控制:
            回滾控制.clear()
        資料庫 = 時鐘 = 識別碼工廠 = 加密 = None
        現在時間 = 正規化允許清單 = 資料庫URI = 連線 = 擁有者列 = None
        憑證識別碼 = issued = 允許清單JSON = None
        del 端點識別碼, 行動者, 名稱, 用途, 到期時間, IP允許清單, 速率限制請求數
        if 主要控制:
            result = None
            _重新拋出控制(主要控制.pop())
        if 回滾控制:
            result = None
            _重新拋出控制(回滾控制.pop())
        if 關閉控制:
            result = None
            _重新拋出控制(關閉控制.pop())
        if not committed or result is None or 失敗類型 is not None:
            result = None
            選定錯誤類型 = 失敗類型 or 憑證管理操作錯誤
            錯誤訊息 = "找不到端點或憑證" if 選定錯誤類型 is 找不到端點憑證錯誤 else (
                "端點生命週期衝突" if 選定錯誤類型 is 端點生命週期衝突錯誤 else "憑證建立失敗"
            )
            失敗類型 = 選定錯誤類型 = None
            raise (找不到端點憑證錯誤(錯誤訊息) if 錯誤訊息 == "找不到端點或憑證" else (
                端點生命週期衝突錯誤(錯誤訊息) if 錯誤訊息 == "端點生命週期衝突"
                else 憑證管理操作錯誤(錯誤訊息)
            )) from None
        return result
    @staticmethod
    def _驗證輸入(endpoint_id, actor, name, purpose, now, expires_at, rate_limit_requests) -> None:
        if (
            type(endpoint_id) is not str
            or type(actor) is not WebOwnerPrincipal
            or type(name) is not str or name != name.strip() or not 1 <= len(name) <= 256
            or type(purpose) is not str or purpose != purpose.strip() or not 1 <= len(purpose) <= 2048
            or any(ord(char) < 32 for char in name + purpose)
            or _像secret(name) or _像secret(purpose)
            or not math.isfinite(now) or now < 0
            or type(expires_at) not in (int, float) or not math.isfinite(float(expires_at)) or expires_at <= now
            or type(rate_limit_requests) is not int or not 1 <= rate_limit_requests <= 10_000
        ):
            raise 憑證管理操作錯誤("憑證建立失敗")

    @staticmethod
    def _正規化allowlist(values: tuple[str, ...]) -> tuple[str, ...]:
        return _正規化allowlist(values)


SQLite憑證儲存庫.建立管理憑證.__signature__ = inspect.signature(SQLite憑證儲存庫.建立)


def _正規化allowlist(values: tuple[str, ...]) -> tuple[str, ...]:
    """以ipaddress產生sorted/dedup canonical IP或network字串。"""
    if type(values) is not tuple or len(values) > 256:
        raise 憑證管理操作錯誤("憑證建立失敗")
    normalized = set()
    for value in values:
        if type(value) is not str or not 1 <= len(value) <= 128 or "%" in value:
            raise 憑證管理操作錯誤("憑證建立失敗")
        parsed = ipaddress.ip_network(value, strict=False) if "/" in value else ipaddress.ip_address(value)
        normalized.add(str(parsed))
    return tuple(sorted(normalized, key=lambda item: (ipaddress.ip_network(item, strict=False).version, item)))


def 註冊憑證SQLite函式(connection: sqlite3.Connection) -> None:
    """註冊published schema canonical UDF；未註冊writer會fail closed。"""
    if type(connection) is not sqlite3.Connection:
        raise 憑證管理操作錯誤("憑證建立失敗") from None
    connection.create_function("published_ip_allowlist_valid", 1, _allowlist_json有效, deterministic=True)
    connection.create_function(
        "published_audit_metadata_canonical", 1, _audit_metadata_json有效, deterministic=True,
    )


def _allowlist_json有效(value) -> int:
    """SQLite UDF：只接受與application canonicalizer完全相同的JSON。"""
    try:
        parsed = json.loads(value)
        if type(parsed) is not list or any(type(item) is not str for item in parsed):
            return 0
        normalized = _正規化allowlist(tuple(parsed))
        canonical = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
        return int(canonical == value)
    except Exception:
        return 0


def _audit_metadata_json有效(value) -> int:
    """拒絕duplicate key，並要求與Python AuditMetadata writer逐byte相同。"""
    try:
        pairs = json.loads(value, object_pairs_hook=lambda items: items)
        if type(pairs) is not list or any(type(item) is not tuple or len(item) != 2 for item in pairs):
            return 0
        if len({item[0] for item in pairs}) != len(pairs):
            return 0
        canonical = json.dumps(
            AuditMetadata(dict(pairs)).to_json(), ensure_ascii=True, separators=(",", ":"),
        )
        return int(canonical == value)
    except Exception:
        return 0


def _像secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("pk_", "sk_", "sk-", "bearer")) or (
        len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)
    )
