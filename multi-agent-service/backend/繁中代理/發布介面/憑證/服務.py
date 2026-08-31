"""Endpoint-bound API key驗證與credential狀態分類。"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from .加密 import APIKey格式有效
from .加密 import AESGCM密文, AESGCM憑證封套
from .儲存庫 import 註冊憑證SQLite函式
from ..協定 import AuditEventSink
from ..契約 import 附加稽核事件或失敗關閉
from ..領域模型 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef, WebOwnerPrincipal


class 憑證驗證錯誤(RuntimeError):
    """固定基礎設施錯誤；不可混成caller的invalid credential。"""


class 憑證揭露錯誤(RuntimeError):
    """owner lookup、audit或decrypt失敗的固定揭露錯誤。"""


class 憑證撤銷錯誤(RuntimeError):
    """scope、authorization、audit或transaction失敗的固定錯誤。"""


class 憑證撤銷找不到錯誤(憑證撤銷錯誤):
    """管理adapter專用；missing、wrong composite與foreign皆相同。"""


class 憑證驗證狀態(str, Enum):
    """HTTP mapper可直接採用的credential authentication codes。"""

    有效 = "authenticated"
    無效 = "invalid_api_key"
    已過期 = "api_key_expired"
    已撤銷 = "api_key_revoked"


class 憑證刷新狀態(str, Enum):
    """D19 hook結果；失敗由caller記錄但不得掩蓋原response。"""

    已刷新 = "refreshed"
    無變更 = "unchanged"
    已略過 = "skipped"
    失敗 = "failed"


_閒置秒數 = 15_552_000
_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _清除控制例外鏈(控制例外: BaseException) -> None:
    """保留exact control與args，但移除可傳遞舊敏感frame的鏈。"""
    BaseException.__setattr__(控制例外, "__traceback__", None)
    BaseException.__setattr__(控制例外, "__cause__", None)
    BaseException.__setattr__(控制例外, "__context__", None)
    BaseException.__setattr__(控制例外, "__suppress_context__", True)


def _重拋已清理控制(控制例外: BaseException) -> None:
    """以空caller box重拋，避免本helper local保留control args。"""
    _清除控制例外鏈(控制例外)
    try:
        raise 控制例外
    except _控制例外:
        del 控制例外
        raise


def _清理揭露連線(連線, 是否回滾: bool) -> list[BaseException]:
    """rollback後必定close；cleanup control採rollback優先。"""
    回滾控制盒: list[BaseException] = []
    關閉控制盒: list[BaseException] = []
    try:
        if 是否回滾 and 連線.in_transaction:
            連線.execute("ROLLBACK")
    except _控制例外 as 控制例外:
        _清除控制例外鏈(控制例外)
        回滾控制盒.append(控制例外)
    except BaseException:
        pass
    try:
        連線.close()
    except _控制例外 as 控制例外:
        _清除控制例外鏈(控制例外)
        關閉控制盒.append(控制例外)
    except BaseException:
        pass
    連線 = None
    if 回滾控制盒:
        關閉控制盒.clear()
        return 回滾控制盒
    return 關閉控制盒


@dataclass(frozen=True, slots=True)
class 憑證驗證結果:
    """不含raw/hash/cipher的typed classifier result。"""

    status: 憑證驗證狀態
    credential_id: str | None = None
    endpoint_id: str | None = None
    endpoint_status: str | None = None
    current_version_id: str | None = None
    credential_rate_limit: int | None = None
    endpoint_rate_limit: int | None = None
    _proof: bytes | None = field(default=None, repr=False, compare=False)

    @classmethod
    def invalid(cls) -> "憑證驗證結果":
        return cls(憑證驗證狀態.無效)


@dataclass(frozen=True, slots=True)
class 明文憑證結果:
    """只供audited reveal回傳；raw key不進repr。"""

    credential_id: str
    api_key: str = field(repr=False)
    key_prefix: str
    key_last4: str


@dataclass(frozen=True, slots=True)
class 憑證撤銷結果:
    """成功撤銷或idempotent no-op的安全結果。"""

    credential_id: str
    revoked_at: float
    already_revoked: bool


class SQLite憑證驗證服務:
    """以hash+endpoint lookup分類，不解密、不寫入、不刷新last_used。"""

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not callable(clock):
            raise 憑證驗證錯誤("憑證驗證失敗") from None
        self._database = Path(database)
        self._clock = clock
        self._proof_key = secrets.token_bytes(32)

    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果:
        """驗證raw key只屬指定endpoint；revoked優先於expired。"""
        key_hash = self._計算候選hash(endpoint_id, presented_api_key)
        if key_hash is None:
            del presented_api_key
            return 憑證驗證結果.invalid()
        connection: sqlite3.Connection | None = None
        result: 憑證驗證結果 | None = None
        failed = False
        try:
            now = float(self._clock())
            if not math.isfinite(now) or now < 0:
                raise 憑證驗證錯誤("憑證驗證失敗")
            database_uri = self._database.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(database_uri, timeout=30, uri=True)
            row = connection.execute(
                "SELECT c.id,c.endpoint_id,c.expires_at,c.revoked_at,c.rate_limit_requests,"
                "e.status,e.current_version_id,e.rate_limit_requests,c.created_at,c.last_used_at "
                "FROM endpoint_credentials c JOIN published_endpoints e ON e.id=c.endpoint_id "
                "WHERE c.key_hash=? AND c.endpoint_id=?",
                (key_hash, endpoint_id),
            ).fetchone()
            if row is None:
                result = 憑證驗證結果.invalid()
            else:
                self._驗證資料shape(row, now)
                status = 憑證驗證狀態.有效
                if row[3] is not None:
                    status = 憑證驗證狀態.已撤銷
                elif now >= float(row[2]):
                    status = 憑證驗證狀態.已過期
                elif now >= float(row[9] if row[9] is not None else row[8]) + _閒置秒數:
                    status = 憑證驗證狀態.無效
                result = 憑證驗證結果(
                    status=status,
                    credential_id=row[0], endpoint_id=row[1], endpoint_status=row[5],
                    current_version_id=row[6], credential_rate_limit=row[4], endpoint_rate_limit=row[7],
                    _proof=self._建立proof(row[0], row[1]) if status is 憑證驗證狀態.有效 else None,
                )
        except Exception:
            failed = True
        finally:
            del presented_api_key, key_hash
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    failed = failed or result is None
        if failed or result is None:
            result = None
            raise 憑證驗證錯誤("憑證驗證失敗") from None
        return result

    def 刷新已認證使用(
        self,
        authentication: 憑證驗證結果,
        request_authenticated_at: float,
    ) -> 憑證刷新狀態:
        """D19=A：進main pipeline時單調刷新；任何write failure只回失敗。"""
        if (
            type(authentication) is not 憑證驗證結果
            or authentication.status is not 憑證驗證狀態.有效
            or type(authentication.credential_id) is not str or type(authentication.endpoint_id) is not str
            or type(authentication._proof) is not bytes
            or not hmac.compare_digest(
                authentication._proof,
                self._建立proof(authentication.credential_id, authentication.endpoint_id),
            )
            or type(request_authenticated_at) not in (int, float)
            or not math.isfinite(float(request_authenticated_at)) or request_authenticated_at < 0
        ):
            return 憑證刷新狀態.已略過
        connection: sqlite3.Connection | None = None
        outcome = 憑證刷新狀態.失敗
        committed = False
        try:
            observed_now = float(self._clock())
            if not math.isfinite(observed_now) or observed_now < 0:
                return 憑證刷新狀態.失敗
            database_uri = self._database.resolve().as_uri() + "?mode=rw"
            connection = sqlite3.connect(database_uri, timeout=30, isolation_level=None, uri=True)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT created_at,last_used_at,expires_at,revoked_at FROM endpoint_credentials "
                "WHERE id=? AND endpoint_id=?",
                (authentication.credential_id, authentication.endpoint_id),
            ).fetchone()
            if not self._可刷新(row, float(request_authenticated_at), observed_now):
                connection.execute("ROLLBACK")
                return 憑證刷新狀態.已略過
            previous = row[1]
            refreshed_at = max(float(previous), float(request_authenticated_at)) if previous is not None else float(request_authenticated_at)
            if previous is not None and refreshed_at == float(previous):
                connection.execute("COMMIT")
                committed = True
                outcome = 憑證刷新狀態.無變更
            else:
                connection.execute(
                    "UPDATE endpoint_credentials SET last_used_at=?,"
                    "updated_at=CASE WHEN updated_at<? THEN ? ELSE updated_at END,revision=revision+1 "
                    "WHERE id=? AND endpoint_id=?",
                    (refreshed_at, refreshed_at, refreshed_at, authentication.credential_id, authentication.endpoint_id),
                )
                connection.execute("COMMIT")
                committed = True
                outcome = 憑證刷新狀態.已刷新
        except Exception:
            if connection is not None and connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    if not committed:
                        outcome = 憑證刷新狀態.失敗
        return outcome

    @staticmethod
    def _計算候選hash(endpoint_id, presented_api_key) -> str | None:
        if (
            type(endpoint_id) is not str or not 1 <= len(endpoint_id) <= 128
            or not APIKey格式有效(presented_api_key)
        ):
            return None
        try:
            encoded = presented_api_key.encode("ascii")
        except UnicodeError:
            return None
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _驗證資料shape(row, now: float) -> None:
        if (
            type(row) is not tuple or len(row) != 10
            or type(row[0]) is not str or type(row[1]) is not str
            or type(row[2]) not in (int, float) or not math.isfinite(float(row[2])) or row[2] < 0
            or (row[3] is not None and (
                type(row[3]) not in (int, float) or not math.isfinite(float(row[3])) or row[3] < 0
            ))
            or type(row[4]) is not int or not 1 <= row[4] <= 10_000
            or row[5] not in ("active", "disabled", "archived")
            or (row[6] is not None and type(row[6]) is not str)
            or type(row[7]) is not int or not 1 <= row[7] <= 10_000
            or type(row[8]) not in (int, float) or not math.isfinite(float(row[8])) or row[8] < 0
            or (row[9] is not None and (
                type(row[9]) not in (int, float) or not math.isfinite(float(row[9]))
                or row[9] < row[8] or row[9] > now
            ))
        ):
            raise 憑證驗證錯誤("憑證驗證失敗")

    @staticmethod
    def _可刷新(row, authenticated_at: float, observed_now: float) -> bool:
        if type(row) is not tuple or len(row) != 4:
            return False
        created, last_used, expires, revoked = row
        if (
            type(created) not in (int, float) or type(expires) not in (int, float)
            or not math.isfinite(float(created)) or not math.isfinite(float(expires))
            or created < 0 or expires < 0 or authenticated_at < float(created)
            or authenticated_at > observed_now or revoked is not None
            or authenticated_at >= float(expires)
        ):
            return False
        if last_used is not None and (
            type(last_used) not in (int, float) or not math.isfinite(float(last_used))
            or last_used < created or last_used > observed_now
        ):
            return False
        baseline = float(last_used) if last_used is not None else float(created)
        return authenticated_at < baseline + _閒置秒數

    def _建立proof(self, credential_id: str, endpoint_id: str) -> bytes:
        payload = credential_id.encode("utf-8") + b"\0" + endpoint_id.encode("utf-8")
        return hmac.new(self._proof_key, payload, hashlib.sha256).digest()


class SQLite憑證揭露服務:
    """owner-scoped、audit-before-decrypt的plaintext reveal boundary。"""

    def __init__(
        self,
        database: str | Path,
        envelope: AESGCM憑證封套,
        audit_sink: AuditEventSink,
        *,
        clock: Callable[[], float] = time.time,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if (
            type(envelope) is not AESGCM憑證封套 or not callable(clock)
            or (event_id_factory is not None and not callable(event_id_factory))
        ):
            raise 憑證揭露錯誤("憑證揭露失敗") from None
        self._database = Path(database)
        self._envelope = envelope
        self._audit_sink = audit_sink
        self._clock = clock
        self._event_id_factory = event_id_factory or (lambda: f"audit-{secrets.token_hex(16)}")

    def 揭露(
        self,
        endpoint_id: str,
        credential_id: str,
        actor: WebOwnerPrincipal,
        request_id: str,
    ) -> 明文憑證結果:
        """audit authorized attempt後鎖定重驗，才decrypt並交付plaintext。"""
        row = current_row = plaintext = result = None
        稽核事件 = 稽核收據 = 密文封套 = 解密回呼 = None
        發生時間 = occurred_at = 資料庫URI = 擁有者使用者識別 = None
        failed = committed = False
        連線: sqlite3.Connection | None = None
        主要控制盒: list[BaseException] = []
        清理控制盒: list[BaseException] = []
        try:
            if type(actor) is not WebOwnerPrincipal:
                raise 憑證揭露錯誤("憑證揭露失敗")
            擁有者使用者識別 = actor.user_id
            發生時間 = float(self._clock())
            occurred_at = 發生時間
            資料庫URI = self._database.resolve().as_uri()
            連線 = sqlite3.connect(資料庫URI + "?mode=ro", timeout=30, uri=True)
            row = self._讀取owner憑證(連線, endpoint_id, credential_id, 擁有者使用者識別)
            if not self._row_valid(row, 發生時間):
                raise 憑證揭露錯誤("憑證揭露失敗")
            初始連線 = 連線
            連線 = None
            清理控制盒 = _清理揭露連線(初始連線, False)
            初始連線 = None
            if 清理控制盒:
                _重拋已清理控制(清理控制盒.pop())
            稽核事件 = AuditEvent(
                event_id=self._event_id_factory(), occurred_at=occurred_at,
                action="credential.reveal_attempt", outcome="success",
                actor=AuditActorRef("user", 擁有者使用者識別),
                resource=AuditResourceRef("endpoint_credential", credential_id),
                request_id=request_id, endpoint_id=endpoint_id,
                metadata=AuditMetadata({"key_version": row[0], "reveal": True}),
            )
            稽核收據 = 附加稽核事件或失敗關閉(self._audit_sink, 稽核事件)
            連線 = sqlite3.connect(
                資料庫URI + "?mode=rw", timeout=30, isolation_level=None, uri=True,
            )
            連線.execute("BEGIN IMMEDIATE")
            current_row = self._讀取owner憑證(連線, endpoint_id, credential_id, 擁有者使用者識別)
            if current_row != row or not self._row_valid(current_row, float(self._clock())):
                raise 憑證揭露錯誤("憑證揭露失敗")
            密文封套 = AESGCM密文(current_row[0], current_row[1], current_row[2])
            解密回呼 = self._envelope.解密
            plaintext = 解密回呼(密文封套, endpoint_id, credential_id)
            if (
                not APIKey格式有效(plaintext)
                or not hmac.compare_digest(hashlib.sha256(plaintext.encode("ascii")).hexdigest(), current_row[3])
                or plaintext[:len(current_row[4])] != current_row[4]
                or plaintext[-4:] != current_row[5]
            ):
                raise 憑證揭露錯誤("憑證揭露失敗")
            result = 明文憑證結果(credential_id, plaintext, current_row[4], current_row[5])
            連線.execute("COMMIT")
            committed = True
        except _控制例外 as 控制例外:
            _清除控制例外鏈(控制例外)
            主要控制盒.append(控制例外)
        except BaseException:
            failed = True
        finally:
            if 連線 is not None:
                清理控制盒 = _清理揭露連線(連線, not committed)
        成功結果 = result if not failed and committed and result is not None else None
        del self, endpoint_id, credential_id, actor, request_id
        row = current_row = plaintext = result = None
        稽核事件 = 稽核收據 = 密文封套 = 解密回呼 = None
        發生時間 = occurred_at = 資料庫URI = 擁有者使用者識別 = 連線 = None
        if 主要控制盒:
            成功結果 = None
            清理控制盒.clear()
            _重拋已清理控制(主要控制盒.pop())
        if 清理控制盒:
            成功結果 = None
            _重拋已清理控制(清理控制盒.pop())
        if 成功結果 is None:
            raise 憑證揭露錯誤("憑證揭露失敗") from None
        return 成功結果

    @staticmethod
    def _讀取owner憑證(connection, endpoint_id, credential_id, owner_user_id):
        try:
            return connection.execute(
                "SELECT c.key_version,c.key_nonce,c.key_ciphertext,c.key_hash,c.key_prefix,c.key_last4,"
                "c.expires_at,c.revoked_at,c.created_at,c.last_used_at,c.revision "
                "FROM endpoint_credentials c JOIN published_endpoints e ON e.id=c.endpoint_id "
                "WHERE c.id=? AND c.endpoint_id=? AND e.owner_user_id=?",
                (credential_id, endpoint_id, owner_user_id),
            ).fetchone()
        except _控制例外:
            del connection, endpoint_id, credential_id, owner_user_id
            raise

    @staticmethod
    def _row_valid(row, now: float) -> bool:
        if not (
            type(row) is tuple and len(row) == 11 and math.isfinite(now) and now >= 0
            and type(row[0]) is int and row[0] > 0
            and type(row[1]) is bytes and len(row[1]) == 12
            and type(row[2]) is bytes and len(row[2]) == 62
            and type(row[3]) is str and len(row[3]) == 64
            and type(row[4]) is str and 1 <= len(row[4]) <= 32
            and type(row[5]) is str and len(row[5]) == 4
            and type(row[6]) in (int, float) and type(row[8]) in (int, float)
            and math.isfinite(float(row[6])) and math.isfinite(float(row[8]))
            and row[7] is None and type(row[10]) is int and row[10] >= 0
        ):
            return False
        last_used = row[9]
        if last_used is not None and (
            type(last_used) not in (int, float) or not math.isfinite(float(last_used))
            or last_used < row[8] or last_used > now
        ):
            return False
        baseline = float(last_used) if last_used is not None else float(row[8])
        return row[8] <= now < row[6] and now < baseline + _閒置秒數


class SQLite憑證撤銷服務:
    """endpoint-scoped owner/admin revoke與audit的單一transaction boundary。"""

    def __init__(
        self,
        database: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(clock) or (event_id_factory is not None and not callable(event_id_factory)):
            raise 憑證撤銷錯誤("憑證撤銷失敗") from None
        self._database = Path(database)
        self._clock = clock
        self._event_id_factory = event_id_factory or (lambda: f"audit-{secrets.token_hex(16)}")

    def 撤銷(
        self,
        endpoint_id: str,
        credential_id: str,
        actor: WebOwnerPrincipal,
        request_id: str,
        *,
        actor_is_admin: bool = False,
    ) -> 憑證撤銷結果:
        """owner或admin可撤銷；already-revoked為audited idempotent success。"""
        資料庫, 時鐘 = self._database, self._clock
        事件識別工廠, 寫入稽核 = self._event_id_factory, type(self)._insert_audit
        del self
        連線 = 資料列 = 事件 = 結果 = 行動者識別碼 = 現在時間 = 資料庫URI = None
        已提交 = 是否失敗 = 是否找不到 = False
        主要控制盒: list[BaseException] = []
        清理控制盒: list[BaseException] = []
        try:
            if type(actor) is not WebOwnerPrincipal or type(actor_is_admin) is not bool:
                raise 憑證撤銷錯誤("憑證撤銷失敗")
            行動者識別碼 = actor.user_id
            現在時間 = float(時鐘())
            資料庫URI = 資料庫.resolve().as_uri() + "?mode=rw"
            連線 = sqlite3.connect(
                資料庫URI, timeout=30, isolation_level=None, uri=True,
            )
            連線.execute("PRAGMA foreign_keys=ON")
            註冊憑證SQLite函式(連線)
            連線.execute("BEGIN IMMEDIATE")
            資料列 = 連線.execute(
                "SELECT e.owner_user_id,c.revoked_at,c.created_at,c.updated_at,c.revision "
                "FROM endpoint_credentials c JOIN published_endpoints e ON e.id=c.endpoint_id "
                "WHERE c.id=? AND c.endpoint_id=?",
                (credential_id, endpoint_id),
            ).fetchone()
            if 資料列 is None:
                raise 憑證撤銷找不到錯誤("憑證撤銷失敗")
            if type(資料列) is not tuple or len(資料列) != 5:
                raise 憑證撤銷錯誤("憑證撤銷失敗")
            if not actor_is_admin and (
                type(資料列[0]) is not str or 資料列[0] != 行動者識別碼
            ):
                raise 憑證撤銷找不到錯誤("憑證撤銷失敗")
            if not SQLite憑證撤銷服務._row_valid(資料列, 現在時間):
                raise 憑證撤銷錯誤("憑證撤銷失敗")
            已撤銷 = 資料列[1] is not None
            撤銷時間 = float(資料列[1]) if 已撤銷 else 現在時間
            if not 已撤銷:
                游標 = 連線.execute(
                    "UPDATE endpoint_credentials SET revoked_at=?,updated_at=?,revision=revision+1 "
                    "WHERE id=? AND endpoint_id=? AND revoked_at IS NULL",
                    (現在時間, 現在時間, credential_id, endpoint_id),
                )
                if 游標.rowcount != 1:
                    raise 憑證撤銷錯誤("憑證撤銷失敗")
            事件 = AuditEvent(
                event_id=事件識別工廠(), occurred_at=現在時間,
                action="credential.revoke", outcome="success",
                actor=AuditActorRef("user", 行動者識別碼),
                resource=AuditResourceRef("endpoint_credential", credential_id),
                request_id=request_id, endpoint_id=endpoint_id,
                metadata=AuditMetadata({
                    "already_revoked": 已撤銷,
                    "admin": actor_is_admin,
                }),
            )
            寫入稽核(連線, 事件)
            連線.execute("COMMIT")
            已提交 = True
            結果 = 憑證撤銷結果(credential_id, 撤銷時間, 已撤銷)
        except _控制例外 as 控制例外:
            _清除控制例外鏈(控制例外)
            主要控制盒.append(控制例外)
        except 憑證撤銷找不到錯誤:
            是否找不到 = True
        except BaseException:
            是否失敗 = True
        finally:
            if 連線 is not None:
                if 連線.in_transaction:
                    try:
                        連線.execute("ROLLBACK")
                    except _控制例外 as 控制例外:
                        _清除控制例外鏈(控制例外)
                        清理控制盒.append(控制例外)
                    except BaseException:
                        是否失敗 = True
                        是否找不到 = False
                try:
                    連線.close()
                except _控制例外 as 控制例外:
                    _清除控制例外鏈(控制例外)
                    if not 清理控制盒:
                        清理控制盒.append(控制例外)
                except BaseException:
                    是否失敗 = 是否失敗 or not 已提交
            連線 = 資料列 = 事件 = 行動者識別碼 = 現在時間 = 資料庫URI = None
            資料庫 = 時鐘 = 事件識別工廠 = 寫入稽核 = None
            del endpoint_id, credential_id, actor, request_id, actor_is_admin
        if 主要控制盒:
            結果 = None
            清理控制盒.clear()
            _重拋已清理控制(主要控制盒.pop())
        if 清理控制盒:
            結果 = None
            _重拋已清理控制(清理控制盒.pop())
        if 是否找不到 and not 是否失敗:
            結果 = None
            raise 憑證撤銷找不到錯誤("憑證撤銷失敗") from None
        if 是否失敗 or not 已提交 or 結果 is None:
            結果 = None
            raise 憑證撤銷錯誤("憑證撤銷失敗") from None
        return 結果

    @staticmethod
    def _row_valid(row, now: float) -> bool:
        if type(row) is not tuple or len(row) != 5:
            return False
        owner_id, revoked_at, created_at, updated_at, revision = row
        if not (
            type(owner_id) is str
            and (revoked_at is None or type(revoked_at) in (int, float))
            and type(created_at) in (int, float) and type(updated_at) in (int, float)
            and type(revision) is int and revision >= 0 and math.isfinite(now)
        ):
            return False
        if not math.isfinite(float(created_at)) or not math.isfinite(float(updated_at)):
            return False
        if now < created_at or now < updated_at:
            return False
        return bool(
            revoked_at is None
            or (math.isfinite(float(revoked_at)) and now >= revoked_at)
        )

    @staticmethod
    def _insert_audit(connection: sqlite3.Connection, event: AuditEvent) -> None:
        connection.execute(
            "INSERT INTO audit_events(event_id,occurred_at,action,outcome,actor_type,actor_id,"
            "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.event_id, event.occurred_at, event.action, event.outcome,
                event.actor.actor_type, event.actor.actor_id, event.resource.resource_type,
                event.resource.resource_id, event.request_id, event.endpoint_id,
                event.invocation_id, json.dumps(event.metadata.to_json(), separators=(",", ":")),
                event.occurred_at,
            ),
        )
