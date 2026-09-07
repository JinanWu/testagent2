"""PostgreSQL hash-only Web session repository。"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from .. import PostgreSQL連線
from ..環境設定 import 交易儲存設定
from .網頁工作階段 import (
    網頁CSRF無效,
    網頁使用者,
    網頁工作階段結果,
    網頁未授權,
    網頁管理權限不足,
    網頁認證不可用,
)

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class PostgreSQL網頁工作階段服務:
    """以 ``PostgreSQL連線.交易連線`` 管理 Web sessions，不執行 schema DDL。"""

    def __init__(
        self,
        凍結設定: 交易儲存設定,
        *,
        時鐘: Callable[[], float] = time.time,
        有效秒數: int = 86_400,
        密鑰工廠: Callable[[], str] | None = None,
    ) -> None:
        if type(凍結設定) is not 交易儲存設定 or 凍結設定.後端 != "postgres":
            raise ValueError("PostgreSQL Web工作階段設定無效")
        if type(有效秒數) is not int or not 60 <= 有效秒數 <= 604_800:
            raise ValueError("Web工作階段設定無效")
        self.設定 = 凍結設定
        self._時鐘 = 時鐘
        self._有效秒數 = 有效秒數
        self._密鑰工廠 = 密鑰工廠 or (lambda: secrets.token_urlsafe(32))

    def 讀取有效秒數(self) -> int:
        if type(self) is not PostgreSQL網頁工作階段服務 or type(self._有效秒數) is not int:
            raise ValueError("Web工作階段設定無效")
        return self._有效秒數

    def _時間(self) -> float:
        try:
            值 = self._時鐘()
            if type(值) in (int, float) and math.isfinite(值) and 值 >= 0:
                return float(值)
        except _控制流程:
            raise
        except BaseException:
            pass
        raise 網頁認證不可用("auth_unavailable") from None

    def _產生密鑰(self) -> str:
        try:
            值 = self._密鑰工廠()
            if type(值) is str and 32 <= len(值) <= 512:
                return 值
        except _控制流程:
            raise
        except BaseException:
            pass
        raise 網頁認證不可用("auth_unavailable") from None

    @staticmethod
    def _雜湊(值: str) -> bytes:
        return hashlib.sha256(值.encode("utf-8")).digest()

    @staticmethod
    def _資料庫時間(值: float) -> datetime:
        return datetime.fromtimestamp(值, timezone.utc)

    @staticmethod
    def _秒數(值: Any) -> float:
        if isinstance(值, datetime):
            if 值.tzinfo is None or 值.utcoffset() is None:
                raise ValueError
            return 值.timestamp()
        return float(值)

    @staticmethod
    def _列(列: Any) -> dict[str, Any] | None:
        if 列 is None:
            return None
        if isinstance(列, dict):
            return dict(列)
        raise RuntimeError

    @staticmethod
    def _使用者(列: dict[str, Any]) -> 網頁使用者:
        角色 = 列["roles"]
        if isinstance(角色, str):
            角色 = json.loads(角色)
        if type(角色) is not list or any(type(值) is not str for 值 in 角色):
            raise ValueError
        return 網頁使用者(
            str(列["user_id"]), str(列["username"]), "admin" if "admin" in 角色 else "member",
        )

    def 發行(
        self, 使用者: 網頁使用者, 舊工作階段權杖: str | None = None,
        使用者代理: str | None = None,
    ) -> 網頁工作階段結果:
        if type(使用者) is not 網頁使用者 or (
            舊工作階段權杖 is not None and type(舊工作階段權杖) is not str
        ) or (
            使用者代理 is not None and (type(使用者代理) is not str or len(使用者代理.encode()) > 4096)
        ):
            raise 網頁認證不可用("auth_unavailable") from None
        現在 = self._時間()
        資料庫現在 = self._資料庫時間(現在)
        try:
            with PostgreSQL連線.交易連線(self.設定) as 連線:
                with 連線.cursor() as 游標:
                    if 舊工作階段權杖:
                        游標.execute(
                            "UPDATE web_sessions SET revoked_at=GREATEST(last_seen_at,%s) "
                            "WHERE session_token_hash=%s AND revoked_at IS NULL AND expires_at>%s",
                            (資料庫現在, self._雜湊(舊工作階段權杖), 資料庫現在),
                        )
                    for _ in range(3):
                        工作階段權杖, CSRF權杖 = self._產生密鑰(), self._產生密鑰()
                        識別碼 = "web-" + secrets.token_hex(16)
                        到期 = 現在 + self._有效秒數
                        游標.execute(
                            "INSERT INTO web_sessions(id,user_id,session_token_hash,csrf_token_hash,created_at,"
                            "expires_at,last_seen_at,revoked_at,user_agent_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,NULL,%s) "
                            "ON CONFLICT (session_token_hash) DO NOTHING RETURNING id",
                            (識別碼, 使用者.識別碼, self._雜湊(工作階段權杖), self._雜湊(CSRF權杖),
                             資料庫現在, self._資料庫時間(到期), 資料庫現在,
                             self._雜湊(使用者代理) if 使用者代理 else None),
                        )
                        if 游標.fetchone() is not None:
                            return 網頁工作階段結果(
                                識別碼, 使用者, 工作階段權杖, CSRF權杖, 到期,
                            )
        except _控制流程:
            raise
        except 網頁認證不可用:
            raise
        except BaseException:
            pass
        raise 網頁認證不可用("auth_unavailable") from None

    def _讀取工作階段(self, 游標: Any, 雜湊: bytes, 鎖定: bool) -> dict[str, Any] | None:
        游標.execute(
            "SELECT s.id,s.user_id,s.csrf_token_hash,s.expires_at,s.last_seen_at,s.revoked_at,"
            "u.username,u.roles,u.disabled FROM web_sessions s LEFT JOIN users u ON u.id=s.user_id "
            "WHERE s.session_token_hash=%s AND s.revoked_at IS NULL" + (" FOR UPDATE OF s" if 鎖定 else ""),
            (雜湊,),
        )
        return self._列(游標.fetchone())

    def 驗證身份(self, 工作階段權杖: str) -> 網頁使用者:
        return self._處理(工作階段權杖, None, "validate").使用者

    def 恢復(self, 工作階段權杖: str, CSRF餅乾: str | None) -> 網頁工作階段結果:
        return self._處理(工作階段權杖, CSRF餅乾, "restore")

    def 輪替(self, 工作階段權杖: str, CSRF權杖: str) -> 網頁工作階段結果:
        return self._處理(工作階段權杖, CSRF權杖, "rotate")

    def 撤銷(self, 工作階段權杖: str, CSRF權杖: str) -> 網頁使用者:
        return self._處理(工作階段權杖, CSRF權杖, "revoke").使用者

    def _處理(
        self, 工作階段權杖: str, CSRF權杖: str | None,
        動作: Literal["validate", "restore", "rotate", "revoke"],
    ) -> 網頁工作階段結果:
        if type(工作階段權杖) is not str or not 工作階段權杖:
            raise 網頁未授權("unauthorized") from None
        if CSRF權杖 is not None and type(CSRF權杖) is not str:
            raise 網頁CSRF無效("csrf_invalid") from None
        現在 = self._時間()
        拒絕: str | None = None
        結果: 網頁工作階段結果 | None = None
        try:
            with PostgreSQL連線.交易連線(self.設定) as 連線:
                with 連線.cursor() as 游標:
                    列 = self._讀取工作階段(游標, self._雜湊(工作階段權杖), 動作 != "validate")
                    if 列 is None or 現在 >= self._秒數(列["expires_at"]):
                        拒絕 = "unauthorized"
                    elif type(列.get("disabled")) is not bool or 列["disabled"]:
                        if 動作 != "validate":
                            游標.execute(
                                "UPDATE web_sessions SET revoked_at=GREATEST(last_seen_at,%s) WHERE id=%s",
                                (self._資料庫時間(現在), 列["id"]),
                            )
                        拒絕 = "unauthorized"
                    else:
                        使用者 = self._使用者(列)
                        相符 = CSRF權杖 is not None and secrets.compare_digest(
                            bytes(列["csrf_token_hash"]), self._雜湊(CSRF權杖),
                        )
                        if 動作 not in ("validate", "restore") and not 相符:
                            拒絕 = "csrf_invalid"
                        elif 動作 == "validate":
                            結果 = 網頁工作階段結果(str(列["id"]), 使用者, 到期時間=self._秒數(列["expires_at"]))
                        elif 動作 == "revoke":
                            游標.execute(
                                "UPDATE web_sessions SET revoked_at=GREATEST(last_seen_at,%s) "
                                "WHERE id=%s AND csrf_token_hash=%s AND revoked_at IS NULL",
                                (self._資料庫時間(現在), 列["id"], 列["csrf_token_hash"]),
                            )
                            if 游標.rowcount != 1:
                                拒絕 = "csrf_invalid"
                            else:
                                結果 = 網頁工作階段結果(str(列["id"]), 使用者, 到期時間=self._秒數(列["expires_at"]))
                        else:
                            需輪替 = 動作 == "rotate" or not 相符
                            新權杖 = self._產生密鑰() if 需輪替 else None
                            if 需輪替:
                                assert 新權杖 is not None
                                游標.execute(
                                    "UPDATE web_sessions SET csrf_token_hash=%s,last_seen_at=GREATEST(last_seen_at,%s) "
                                    "WHERE id=%s AND csrf_token_hash=%s AND revoked_at IS NULL",
                                    (self._雜湊(新權杖), self._資料庫時間(現在), 列["id"], 列["csrf_token_hash"]),
                                )
                            else:
                                游標.execute(
                                    "UPDATE web_sessions SET last_seen_at=GREATEST(last_seen_at,%s) "
                                    "WHERE id=%s AND revoked_at IS NULL", (self._資料庫時間(現在), 列["id"]),
                                )
                            if 游標.rowcount != 1:
                                拒絕 = "csrf_invalid"
                            else:
                                結果 = 網頁工作階段結果(
                                    str(列["id"]), 使用者, CSRF權杖=新權杖 or CSRF權杖,
                                    到期時間=self._秒數(列["expires_at"]), csrf已輪替=需輪替,
                                )
        except _控制流程:
            raise
        except (網頁未授權, 網頁CSRF無效):
            raise
        except BaseException:
            raise 網頁認證不可用("auth_unavailable") from None
        if 拒絕 == "unauthorized":
            raise 網頁未授權("unauthorized") from None
        if 拒絕 == "csrf_invalid":
            raise 網頁CSRF無效("csrf_invalid") from None
        if 結果 is None:
            raise 網頁認證不可用("auth_unavailable") from None
        return 結果

    def 授權管理操作(self, 工作階段權杖: str, CSRF權杖: str | None) -> 網頁工作階段結果:
        if type(工作階段權杖) is not str or not 工作階段權杖:
            raise 網頁未授權("unauthorized") from None
        現在 = self._時間()
        拒絕: str | None = None
        結果: 網頁工作階段結果 | None = None
        try:
            with PostgreSQL連線.交易連線(self.設定) as 連線:
                with 連線.cursor() as 游標:
                    列 = self._讀取工作階段(游標, self._雜湊(工作階段權杖), True)
                    if 列 is None or 現在 >= self._秒數(列["expires_at"]):
                        拒絕 = "unauthorized"
                    elif type(列.get("disabled")) is not bool or 列["disabled"]:
                        游標.execute(
                            "UPDATE web_sessions SET revoked_at=GREATEST(last_seen_at,%s) WHERE id=%s",
                            (self._資料庫時間(現在), 列["id"]),
                        )
                        拒絕 = "unauthorized"
                    else:
                        使用者 = self._使用者(列)
                        if 使用者.角色 != "admin":
                            拒絕 = "admin_required"
                        elif type(CSRF權杖) is not str or not secrets.compare_digest(
                            bytes(列["csrf_token_hash"]), self._雜湊(CSRF權杖),
                        ):
                            拒絕 = "csrf_invalid"
                        else:
                            新權杖 = self._產生密鑰()
                            游標.execute(
                                "UPDATE web_sessions SET csrf_token_hash=%s,last_seen_at=GREATEST(last_seen_at,%s) "
                                "WHERE id=%s AND csrf_token_hash=%s AND revoked_at IS NULL",
                                (self._雜湊(新權杖), self._資料庫時間(現在), 列["id"], 列["csrf_token_hash"]),
                            )
                            if 游標.rowcount != 1:
                                拒絕 = "csrf_invalid"
                            else:
                                結果 = 網頁工作階段結果(
                                    str(列["id"]), 使用者, CSRF權杖=新權杖,
                                    到期時間=self._秒數(列["expires_at"]), csrf已輪替=True,
                                )
        except _控制流程:
            raise
        except BaseException:
            raise 網頁認證不可用("auth_unavailable") from None
        if 拒絕 == "unauthorized":
            raise 網頁未授權("unauthorized") from None
        if 拒絕 == "admin_required":
            raise 網頁管理權限不足("admin_required") from None
        if 拒絕 == "csrf_invalid":
            raise 網頁CSRF無效("csrf_invalid") from None
        if 結果 is None:
            raise 網頁認證不可用("auth_unavailable") from None
        return 結果
