"""PostgreSQL endpoint credential repository；資料庫永不保存 plaintext API key。"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ...環境設定 import 交易儲存設定
from ..PostgreSQL工作單元 import PostgreSQL工作單元
from ..憑證管理契約 import (
    憑證管理狀態, 憑證摘要, 憑證列表結果, 憑證建立命令, 一次性憑證建立收據,
    憑證撤銷收據, 找不到端點憑證錯誤, 端點生命週期衝突錯誤, 憑證管理操作錯誤,
)
from .服務 import 憑證驗證結果, 憑證驗證狀態, 憑證刷新狀態
from ..領域模型 import WebOwnerPrincipal
from .加密 import AESGCM憑證封套, APIKey格式有效
from .儲存庫 import _正規化allowlist, _像secret, 建立憑證結果, 憑證儲存錯誤

_閒置秒數 = 15_552_000
_預設速率視窗秒數 = 60


def _get(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if isinstance(row, (tuple, list)) and len(row) > index:
        return row[index]
    return None


def _json_array(value: Any) -> tuple[str, ...]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if type(parsed) is not list or any(type(x) is not str for x in parsed):
        raise ValueError
    return _正規化allowlist(tuple(parsed))


def _時間戳(epoch秒: float) -> datetime:
    return datetime.fromtimestamp(float(epoch秒), timezone.utc)


def _epoch(value: Any) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.timestamp()
    return float(value)


def _JSONB(value: Any) -> Jsonb:
    return Jsonb(value)


def _狀態(row: Any, now: float) -> 憑證管理狀態:
    revoked = _get(row, "revoked_at", 8)
    expires = _epoch(_get(row, "expires_at", 5))
    created = _epoch(_get(row, "created_at", 7))
    last = _get(row, "last_used_at", 6)
    if revoked is not None:
        return 憑證管理狀態.已撤銷
    if now >= expires:
        return 憑證管理狀態.已過期
    if now >= (_epoch(last) if last is not None else created) + _閒置秒數:
        return 憑證管理狀態.閒置
    return 憑證管理狀態.有效


def _摘要(row: Any, now: float) -> 憑證摘要:
    return 憑證摘要(
        _get(row, "id", 0), _get(row, "name", 1), _get(row, "purpose", 2),
        _get(row, "key_prefix", 3), _get(row, "key_last4", 4), _狀態(row, now),
        _epoch(_get(row, "expires_at", 5)),
        None if _get(row, "last_used_at", 6) is None else _epoch(_get(row, "last_used_at", 6)),
        _epoch(_get(row, "created_at", 7)),
        None if _get(row, "revoked_at", 8) is None else _epoch(_get(row, "revoked_at", 8)),
        _json_array(_get(row, "ip_allowlist", 9)),
        _get(row, "rate_limit_requests", 10),
    )


class PostgreSQL憑證儲存庫:
    """Owner-isolated create/list/validate/revoke credential operations。"""

    __slots__ = ("_工作單元", "_封套", "_時鐘", "_識別工廠", "_事件工廠", "_proof_key")

    def __init__(
        self, 設定: 交易儲存設定, envelope: AESGCM憑證封套, *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if (type(envelope) is not AESGCM憑證封套 or not callable(clock)
                or (id_factory is not None and not callable(id_factory))
                or (event_id_factory is not None and not callable(event_id_factory))):
            raise 憑證管理操作錯誤("憑證管理失敗") from None
        self._工作單元 = PostgreSQL工作單元(設定)
        self._封套 = envelope
        self._時鐘 = clock
        self._識別工廠 = id_factory or (lambda: f"cred-{secrets.token_hex(16)}")
        self._事件工廠 = event_id_factory or (lambda: f"audit-{secrets.token_hex(16)}")
        self._proof_key = secrets.token_bytes(32)

    def 建立憑證(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 請求: 憑證建立命令,
    ) -> 一次性憑證建立收據:
        """只在成功回傳物件交付 plaintext；SQL 僅含 envelope/hash/preview。"""
        try:
            if type(請求) is not 憑證建立命令 or type(擁有者使用者識別碼) is not str:
                raise 憑證管理操作錯誤("憑證建立失敗")
            now = float(self._時鐘())
            if not math.isfinite(now) or now < 0 or 請求.到期時間 <= now:
                raise 憑證管理操作錯誤("憑證建立失敗")
            credential_id = self._識別工廠()
            if type(credential_id) is not str:
                raise 憑證管理操作錯誤("憑證建立失敗")
            issued = self._封套.產生並加密(端點識別碼, credential_id)
            allowlist = _正規化allowlist(請求.IP允許清單)
            with self._工作單元.交易() as conn:
                endpoint = conn.execute(
                    "SELECT status FROM published_endpoints "
                    "WHERE id=%s AND owner_user_id=%s FOR UPDATE",
                    (端點識別碼, 擁有者使用者識別碼),
                ).fetchone()
                if endpoint is None:
                    raise 找不到端點憑證錯誤("找不到端點或憑證")
                if _get(endpoint, "status", 0) != "active":
                    raise 端點生命週期衝突錯誤("端點生命週期衝突")
                conn.execute(
                    "INSERT INTO endpoint_credentials("
                    "id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,"
                    "expires_at,last_used_at,revoked_at,inactive_disabled_at,ip_allowlist,"
                    "rate_limit_requests,rate_limit_window_seconds,created_by_user_id,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s)",
                    (credential_id, 端點識別碼, 請求.名稱, 請求.用途,
                     issued.envelope.key_version, bytes(issued.envelope.nonce), bytes(issued.envelope.ciphertext),
                     issued.key_hash, issued.key_prefix, issued.key_last4, _時間戳(請求.到期時間),
                     _JSONB(list(allowlist)), 請求.速率限制請求數, _預設速率視窗秒數,
                     擁有者使用者識別碼, _時間戳(now)),
                )
            return 一次性憑證建立收據(
                credential_id, 請求.名稱, 請求.用途, issued.key_prefix, issued.key_last4,
                憑證管理狀態.有效, float(請求.到期時間), None, now, None, allowlist,
                請求.速率限制請求數, issued.api_key,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 找不到端點憑證錯誤,
                端點生命週期衝突錯誤, 憑證管理操作錯誤):
            raise
        except BaseException:
            raise 憑證管理操作錯誤("憑證建立失敗") from None

    def 建立(
        self, endpoint_id: str, actor: WebOwnerPrincipal, *, name: str, purpose: str,
        expires_at: float, ip_allowlist: tuple[str, ...] = (), rate_limit_requests: int = 60,
    ) -> 建立憑證結果:
        """相容既有 SQLite repository 的英文參數與 DTO。"""
        try:
            if type(actor) is not WebOwnerPrincipal:
                raise ValueError
            command = 憑證建立命令(
                name, purpose, float(expires_at), _正規化allowlist(ip_allowlist), rate_limit_requests,
            )
            receipt = self.建立憑證(
                端點識別碼=endpoint_id, 擁有者使用者識別碼=actor.user_id, 請求=command,
            )
            return 建立憑證結果(
                receipt.憑證識別碼, receipt.初始金鑰, receipt.名稱, receipt.用途,
                receipt.金鑰前綴, receipt.金鑰末四碼, receipt.到期時間,
                receipt.IP允許清單, receipt.速率限制請求數, receipt.建立時間,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 憑證儲存錯誤("憑證建立失敗") from None

    建立管理憑證 = 建立

    def 列出憑證(
        self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 是否管理者: bool = False,
    ) -> 憑證列表結果:
        try:
            if type(是否管理者) is not bool:
                raise 憑證管理操作錯誤("憑證管理失敗")
            now = float(self._時鐘())
            with self._工作單元.交易() as conn:
                endpoint = conn.execute(
                    "SELECT owner_user_id FROM published_endpoints WHERE id=%s",
                    (端點識別碼,),
                ).fetchone()
                if endpoint is None or (not 是否管理者 and _get(endpoint, "owner_user_id", 0) != 擁有者使用者識別碼):
                    raise 找不到端點憑證錯誤("找不到端點或憑證")
                rows = conn.execute(
                    "SELECT id,name,purpose,key_prefix,key_last4,expires_at,last_used_at,created_at,"
                    "revoked_at,ip_allowlist,rate_limit_requests FROM endpoint_credentials "
                    "WHERE endpoint_id=%s ORDER BY created_at,id", (端點識別碼,),
                ).fetchall()
            return 憑證列表結果(tuple(_摘要(row, now) for row in rows))
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 找不到端點憑證錯誤):
            raise
        except BaseException:
            raise 憑證管理操作錯誤("憑證管理失敗") from None

    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果:
        """Return the canonical typed verifier result without exposing the API key."""
        if type(endpoint_id) is not str or not APIKey格式有效(presented_api_key):
            return 憑證驗證結果.invalid()
        digest = hashlib.sha256(presented_api_key.encode("ascii")).hexdigest()
        del presented_api_key
        try:
            now = float(self._時鐘())
            with self._工作單元.交易() as conn:
                row = conn.execute(
                    "SELECT c.id,c.expires_at,c.revoked_at,c.rate_limit_requests AS credential_rate_limit,e.status,e.current_version_id,e.rate_limit_requests AS endpoint_rate_limit,c.created_at,c.last_used_at "
                    "FROM endpoint_credentials c JOIN published_endpoints e ON e.id=c.endpoint_id "
                    "WHERE c.endpoint_id=%s AND c.key_hash=%s", (endpoint_id, digest),
                ).fetchone()
            if row is None:
                return 憑證驗證結果.invalid()
            status = 憑證驗證狀態.有效
            if _get(row, "revoked_at", 2) is not None:
                status = 憑證驗證狀態.已撤銷
            elif now >= _epoch(_get(row, "expires_at", 1)):
                status = 憑證驗證狀態.已過期
            elif now >= _epoch(_get(row, "last_used_at", 8) or _get(row, "created_at", 7)) + _閒置秒數:
                status = 憑證驗證狀態.無效
            credential_id = _get(row, "id", 0)
            proof = hmac.digest(self._proof_key, f"{credential_id}:{endpoint_id}".encode(), "sha256") if status is 憑證驗證狀態.有效 else None
            return 憑證驗證結果(status, credential_id, endpoint_id, _get(row, "status", 4), _get(row, "current_version_id", 5), _get(row, "credential_rate_limit", 3), _get(row, "endpoint_rate_limit", 6), proof)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 憑證管理操作錯誤("憑證驗證失敗") from None

    def 刷新已認證使用(self, authentication: 憑證驗證結果, request_authenticated_at: float) -> 憑證刷新狀態:
        """Monotonically update last_used_at after a typed successful verification."""
        if (type(authentication) is not 憑證驗證結果 or authentication.status is not 憑證驗證狀態.有效
                or type(authentication.credential_id) is not str or type(authentication.endpoint_id) is not str
                or type(authentication._proof) is not bytes or not isinstance(request_authenticated_at, (int, float))):
            return 憑證刷新狀態.已略過
        expected = hmac.digest(self._proof_key, f"{authentication.credential_id}:{authentication.endpoint_id}".encode(), "sha256")
        if not hmac.compare_digest(authentication._proof, expected):
            return 憑證刷新狀態.已略過
        try:
            with self._工作單元.交易() as conn:
                result = conn.execute(
                    "UPDATE endpoint_credentials SET last_used_at=GREATEST(COALESCE(last_used_at,to_timestamp(%s)),to_timestamp(%s)) WHERE id=%s AND endpoint_id=%s AND revoked_at IS NULL",
                    (request_authenticated_at, request_authenticated_at, authentication.credential_id, authentication.endpoint_id),
                )
                return 憑證刷新狀態.已刷新 if getattr(result, "rowcount", 0) == 1 else 憑證刷新狀態.失敗
        except BaseException:
            return 憑證刷新狀態.失敗

    def 撤銷憑證(
        self, *, 端點識別碼: str, 憑證識別碼: str, 擁有者使用者識別碼: str,
        是否管理者: bool, 請求識別碼: str,
    ) -> 憑證撤銷收據:
        try:
            if type(是否管理者) is not bool:
                raise 憑證管理操作錯誤("憑證管理失敗")
            now = float(self._時鐘())
            with self._工作單元.交易() as conn:
                row = conn.execute(
                    "SELECT e.owner_user_id,c.revoked_at FROM endpoint_credentials c "
                    "JOIN published_endpoints e ON e.id=c.endpoint_id "
                    "WHERE c.id=%s AND c.endpoint_id=%s FOR UPDATE OF c",
                    (憑證識別碼, 端點識別碼),
                ).fetchone()
                if row is None or (not 是否管理者 and _get(row, "owner_user_id", 0) != 擁有者使用者識別碼):
                    raise 找不到端點憑證錯誤("找不到端點或憑證")
                revoked = _get(row, "revoked_at", 1)
                already = revoked is not None
                revoked_at = _epoch(revoked) if already else now
                if not already:
                    cursor = conn.execute(
                        "UPDATE endpoint_credentials SET revoked_at=%s "
                        "WHERE id=%s AND endpoint_id=%s AND revoked_at IS NULL",
                        (_時間戳(now), 憑證識別碼, 端點識別碼),
                    )
                    if cursor.rowcount != 1:
                        raise 憑證管理操作錯誤("憑證管理失敗")
                event_id = self._事件工廠()
                conn.execute(
                    "INSERT INTO audit_events("
                    "id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,"
                    "request_id,endpoint_id,invocation_id,metadata,created_at) "
                    "VALUES(%s,%s,%s,'credential.revoke','success','user',%s,'endpoint_credential',"
                    "%s,%s,%s,NULL,%s,%s)",
                    (event_id, event_id, _時間戳(now), 擁有者使用者識別碼, 憑證識別碼,
                     請求識別碼, 端點識別碼,
                     _JSONB({"already_revoked": already, "admin": 是否管理者}), _時間戳(now)),
                )
            return 憑證撤銷收據(憑證識別碼, revoked_at, already)
        except (KeyboardInterrupt, SystemExit, GeneratorExit, 找不到端點憑證錯誤):
            raise
        except BaseException:
            raise 憑證管理操作錯誤("憑證管理失敗") from None


PostgreSQL憑證庫 = PostgreSQL憑證儲存庫
