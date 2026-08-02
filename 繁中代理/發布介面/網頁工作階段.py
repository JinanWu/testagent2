"""獨立於 CLI auth_sessions 的 hash-only Web session 服務。"""
from __future__ import annotations
import hashlib
import json
import math
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal
class 網頁未授權(RuntimeError):
    """Cookie session 不存在、已撤銷、過期或 owner 無效。"""
class 網頁CSRF無效(RuntimeError):
    """CSRF 缺少、不符或已使用。"""
class 網頁認證不可用(RuntimeError):
    """Web 認證儲存層不可用或資料不可信。"""
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
def _清除例外鏈(錯誤: BaseException) -> None:
    """不呼叫 hostile override 地移除可能帶敏感資料的例外鏈。"""
    BaseException.__setattr__(錯誤, "__cause__", None)
    BaseException.__setattr__(錯誤, "__context__", None)
    BaseException.__setattr__(錯誤, "__suppress_context__", True)
def _重拋控制流程(錯誤: BaseException) -> None:
    """保留 KISG identity/args，但 rethrow frame 不保留其物件。"""
    try:
        raise 錯誤
    except _控制流程:
        錯誤 = None
        raise
def _清理連線(連線: object, 需回滾: bool, 回滾控制: list, 關閉控制: list) -> None:
    """rollback/close 各至多一次；ordinary cleanup failure 不取代 primary。"""
    try:
        if 需回滾:
            try:
                連線.execute("ROLLBACK")
            except _控制流程 as 錯誤:
                _清除例外鏈(錯誤)
                回滾控制.append(錯誤)
                錯誤 = None
            except BaseException:
                pass
        try:
            連線.close()
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            關閉控制.append(錯誤)
            錯誤 = None
        except BaseException:
            pass
    finally:
        連線 = 需回滾 = 回滾控制 = 關閉控制 = None
@dataclass(frozen=True, slots=True)
class 網頁使用者:
    """只供 UI 顯示、不得作授權依據的最小 principal。"""
    識別碼: str
    使用者名稱: str
    角色: str
    def __post_init__(self) -> None:
        """限制 W102 DTO 欄位。"""
        if not (
            type(self.識別碼) is str and 1 <= len(self.識別碼) <= 128
            and type(self.使用者名稱) is str and 1 <= len(self.使用者名稱) <= 128
            and type(self.角色) is str and 1 <= len(self.角色) <= 64
        ):
            raise ValueError("網頁使用者無效")
@dataclass(frozen=True, slots=True)
class 網頁工作階段結果:
    """明文 secret 僅存在於一次性服務結果且不進 repr。"""
    識別碼: str
    使用者: 網頁使用者
    工作階段權杖: str | None = field(default=None, repr=False)
    CSRF權杖: str | None = field(default=None, repr=False)
    到期時間: float = 0.0
    csrf已輪替: bool = False
class 網頁工作階段服務:
    """以 request-local SQLite transaction 管理 Web session。"""
    def __init__(
        self,
        資料庫路徑: str | Path,
        *,
        時鐘: Callable[[], float] = time.time,
        有效秒數: int = 86_400,
        密鑰工廠: Callable[[], str] | None = None,
    ) -> None:
        """保存不可變連線參數，不建立或改寫 schema。"""
        if type(有效秒數) is not int or not 60 <= 有效秒數 <= 604_800:
            raise ValueError("Web工作階段設定無效")
        self._路徑 = Path(資料庫路徑)
        self._時鐘 = 時鐘
        self._有效秒數 = 有效秒數
        self._密鑰工廠 = 密鑰工廠 or (lambda: secrets.token_urlsafe(32))
    def 讀取有效秒數(self) -> int:
        """由 class-bound 呼叫讀取 authoritative TTL，不執行 instance property。"""
        if type(self) is not 網頁工作階段服務 or type(self._有效秒數) is not int:
            raise ValueError("Web工作階段設定無效")
        return self._有效秒數
    def _時間(self) -> float:
        """只接受非負有限 exact number。"""
        try:
            值 = self._時鐘()
            try:
                有效 = type(值) in (int, float) and math.isfinite(值) and 值 >= 0
            except (OverflowError, TypeError, ValueError):
                有效 = False
            if 有效:
                return float(值)
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            self = 值 = 有效 = None
            raise
        except BaseException:
            pass
        finally:
            self = 值 = 有效 = None
        raise 網頁認證不可用("auth_unavailable") from None
    def _產生密鑰(self) -> str:
        """要求 URL-safe 工廠提供足夠長、bounded 的 exact str。"""
        try:
            值 = self._密鑰工廠()
            if type(值) is str and 32 <= len(值) <= 512:
                return 值
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            self = 值 = None
            raise
        except BaseException:
            pass
        finally:
            self = 值 = None
        raise 網頁認證不可用("auth_unavailable") from None
    @staticmethod
    def _雜湊(值: str) -> bytes:
        """SHA-256 digest；SQLite 永不保存 plaintext。"""
        try:
            return hashlib.sha256(值.encode("utf-8")).digest()
        finally:
            值 = None
    def _連線(self) -> sqlite3.Connection:
        """建立 request-local connection 與固定 pragma。"""
        連線 = None
        完成 = False
        try:
            連線 = sqlite3.connect(self._路徑, timeout=1, isolation_level=None)
            連線.row_factory = sqlite3.Row
            連線.execute("PRAGMA foreign_keys=ON")
            連線.execute("PRAGMA busy_timeout=1000")
            完成 = True
            return 連線
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            if 連線 is not None:
                try:
                    連線.close()
                except BaseException:
                    pass
            self = 連線 = 完成 = None
            _清除例外鏈(錯誤)
            錯誤 = None
            raise
        except BaseException:
            if 連線 is not None:
                try:
                    連線.close()
                except _控制流程 as 錯誤:
                    _清除例外鏈(錯誤)
                    self = 連線 = 完成 = None
                    raise
                except BaseException:
                    pass
        finally:
            self = None
            if not 完成:
                連線 = None
        raise 網頁認證不可用("auth_unavailable") from None
    def 發行(
        self,
        使用者: 網頁使用者,
        舊工作階段權杖: str | None = None,
        使用者代理: str | None = None,
    ) -> 網頁工作階段結果:
        """同一 BEGIN IMMEDIATE 撤銷 presented cookie 並發行全新 pair。"""
        try:
            return self._發行核心(使用者, 舊工作階段權杖, 使用者代理)
        finally:
            self = 使用者 = 舊工作階段權杖 = 使用者代理 = None

    def _發行核心(self, 使用者, 舊工作階段權杖, 使用者代理):
        """清理完整 issuance transaction 後才發布結果或固定錯誤。"""
        現在時間 = 工作階段密鑰 = CSRF密鑰 = 工作階段識別碼 = 結果 = 連線 = None
        已開始 = 已提交 = 失敗 = False
        回滾控制: list[BaseException] = []
        關閉控制: list[BaseException] = []
        try:
            if type(使用者) is not 網頁使用者 or (舊工作階段權杖 is not None and type(舊工作階段權杖) is not str):
                失敗 = True
            elif 使用者代理 is not None and (type(使用者代理) is not str or len(使用者代理.encode()) > 4096):
                失敗 = True
            if not 失敗:
                現在時間 = self._時間()
                工作階段密鑰, CSRF密鑰 = self._產生密鑰(), self._產生密鑰()
                連線 = self._連線()
                連線.execute("BEGIN IMMEDIATE")
                已開始 = True
                if 舊工作階段權杖:
                    連線.execute(
                        "UPDATE web_sessions SET revoked_at=CASE WHEN last_seen_at>? THEN last_seen_at ELSE ? END "
                        "WHERE session_token_hash=? AND revoked_at IS NULL AND expires_at>?",
                        (現在時間, 現在時間, self._雜湊(舊工作階段權杖), 現在時間),
                    )
                for 嘗試次數 in range(3):
                    工作階段識別碼 = "web-" + secrets.token_hex(16)
                    結果 = 網頁工作階段結果(工作階段識別碼, 使用者, 工作階段密鑰, CSRF密鑰, 現在時間 + self._有效秒數)
                    try:
                        連線.execute(
                            "INSERT INTO web_sessions(id,user_id,session_token_hash,csrf_token_hash,created_at,"
                            "expires_at,last_seen_at,revoked_at,user_agent_hash) VALUES(?,?,?,?,?,?,?,NULL,?)",
                            (工作階段識別碼, 使用者.識別碼, self._雜湊(工作階段密鑰), self._雜湊(CSRF密鑰),
                             現在時間, 結果.到期時間, 現在時間, self._雜湊(使用者代理) if 使用者代理 else None),
                        )
                        break
                    except sqlite3.IntegrityError:
                        if 嘗試次數 == 2:
                            raise
                        工作階段密鑰, CSRF密鑰 = self._產生密鑰(), self._產生密鑰()
                連線.execute("COMMIT")
                已提交 = True
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            if 連線 is not None:
                _清理連線(連線, 已開始 and not 已提交, 回滾控制, 關閉控制)
            self = 使用者 = 舊工作階段權杖 = 使用者代理 = 現在時間 = None
            工作階段密鑰 = CSRF密鑰 = 工作階段識別碼 = 結果 = 連線 = None
            回滾控制.clear(); 關閉控制.clear(); _清除例外鏈(錯誤); 錯誤 = None
            raise
        except BaseException:
            失敗 = True
        if 連線 is not None:
            _清理連線(連線, 已開始 and not 已提交, 回滾控制, 關閉控制)
        連線 = None
        if 回滾控制 or 關閉控制:
            self = 使用者 = 舊工作階段權杖 = 使用者代理 = 現在時間 = None
            工作階段密鑰 = CSRF密鑰 = 工作階段識別碼 = 結果 = None
            if 回滾控制:
                關閉控制.clear()
                _重拋控制流程(回滾控制.pop())
            回滾控制.clear()
            _重拋控制流程(關閉控制.pop())
        回滾控制.clear(); 關閉控制.clear()
        if 失敗 or not 已提交 or 結果 is None:
            self = 使用者 = 舊工作階段權杖 = 使用者代理 = 現在時間 = None
            工作階段密鑰 = CSRF密鑰 = 工作階段識別碼 = 結果 = None
            raise 網頁認證不可用("auth_unavailable") from None
        return 結果
    def 恢復(self, 工作階段權杖: str, CSRF餅乾: str | None) -> 網頁工作階段結果:
        """安全 GET 不消耗 matching CSRF；缺少或不符時原子 recovery rotation。"""
        try:
            return self._處理(工作階段權杖, CSRF餅乾, "restore")
        finally:
            self = 工作階段權杖 = CSRF餅乾 = None
    def 輪替(self, 工作階段權杖: str, CSRF權杖: str) -> 網頁工作階段結果:
        """原子消耗目前 CSRF 並回傳 successor。"""
        try:
            return self._處理(工作階段權杖, CSRF權杖, "rotate")
        finally:
            self = 工作階段權杖 = CSRF權杖 = None
    def 撤銷(self, 工作階段權杖: str, CSRF權杖: str) -> 網頁使用者:
        """驗證 CSRF 後原子撤銷，沒有 successor。"""
        try:
            return self._處理(工作階段權杖, CSRF權杖, "revoke").使用者
        finally:
            self = 工作階段權杖 = CSRF權杖 = None
    def _處理(
        self,
        工作階段權杖: str,
        CSRF權杖: str | None,
        動作: Literal["restore", "rotate", "revoke"],
    ) -> 網頁工作階段結果:
        """共用 session/owner/expiry gate 與 single-writer transition。"""
        try:
            return self._處理核心(工作階段權杖, CSRF權杖, 動作)
        finally:
            self = 工作階段權杖 = CSRF權杖 = 動作 = None

    def _處理核心(self, 工作階段權杖, CSRF權杖, 動作):
        """完成 commit/cleanup 並清除所有 authority-bearing intermediate locals。"""
        工作階段雜湊 = CSRF雜湊 = 現在時間 = 連線 = 資料列 = 角色清單 = 使用者 = None
        替代權杖 = 游標 = 結果 = None
        已開始 = 已提交 = 失敗 = 相符 = 需輪替 = False
        拒絕碼 = None
        回滾控制: list[BaseException] = []
        關閉控制: list[BaseException] = []
        try:
            if type(工作階段權杖) is not str or not 工作階段權杖:
                拒絕碼 = "unauthorized"
            elif CSRF權杖 is not None and type(CSRF權杖) is not str:
                拒絕碼 = "csrf_invalid"
            else:
                工作階段雜湊 = self._雜湊(工作階段權杖)
                CSRF雜湊 = self._雜湊(CSRF權杖) if CSRF權杖 is not None else None
                現在時間 = self._時間()
                連線 = self._連線()
                連線.execute("BEGIN IMMEDIATE")
                已開始 = True
                資料列 = 連線.execute(
                    "SELECT s.id,s.user_id,s.csrf_token_hash,s.expires_at,s.last_seen_at,"
                    "u.username,u.roles_json,u.disabled FROM web_sessions s LEFT JOIN users u ON u.id=s.user_id "
                    "WHERE s.session_token_hash=? AND s.revoked_at IS NULL", (工作階段雜湊,),
                ).fetchone()
                if 資料列 is None or 現在時間 >= float(資料列["expires_at"]):
                    拒絕碼 = "unauthorized"
                elif type(資料列["disabled"]) is not int or 資料列["disabled"] != 0:
                    連線.execute(
                        "UPDATE web_sessions SET revoked_at=CASE WHEN last_seen_at>? THEN last_seen_at ELSE ? END WHERE id=?",
                        (現在時間, 現在時間, 資料列["id"]),
                    )
                    拒絕碼 = "unauthorized"
                else:
                    角色清單 = json.loads(資料列["roles_json"])
                    if type(角色清單) is not list or any(type(角色值) is not str for 角色值 in 角色清單):
                        raise ValueError
                    使用者 = 網頁使用者(str(資料列["user_id"]), str(資料列["username"]), "admin" if "admin" in 角色清單 else "member")
                    相符 = CSRF雜湊 is not None and secrets.compare_digest(bytes(資料列["csrf_token_hash"]), CSRF雜湊)
                    if 動作 != "restore" and not 相符:
                        拒絕碼 = "csrf_invalid"
                    else:
                        需輪替 = 動作 == "rotate" or (動作 == "restore" and not 相符)
                        if 需輪替:
                            替代權杖 = self._產生密鑰()
                            游標 = 連線.execute(
                                "UPDATE web_sessions SET csrf_token_hash=?,last_seen_at=CASE WHEN last_seen_at>? "
                                "THEN last_seen_at ELSE ? END WHERE id=? AND csrf_token_hash=?",
                                (self._雜湊(替代權杖), 現在時間, 現在時間, 資料列["id"], 資料列["csrf_token_hash"]),
                            )
                            if 游標.rowcount != 1:
                                拒絕碼 = "csrf_invalid"
                        elif 動作 == "revoke":
                            連線.execute(
                                "UPDATE web_sessions SET revoked_at=CASE WHEN last_seen_at>? THEN last_seen_at ELSE ? END WHERE id=?",
                                (現在時間, 現在時間, 資料列["id"]),
                            )
                        else:
                            連線.execute(
                                "UPDATE web_sessions SET last_seen_at=CASE WHEN last_seen_at>? THEN last_seen_at ELSE ? END WHERE id=?",
                                (現在時間, 現在時間, 資料列["id"]),
                            )
                        if 拒絕碼 is None:
                            結果 = 網頁工作階段結果(str(資料列["id"]), 使用者, CSRF權杖=替代權杖 or CSRF權杖,
                                                  到期時間=float(資料列["expires_at"]), csrf已輪替=需輪替)
                連線.execute("COMMIT")
                已提交 = True
        except _控制流程 as 錯誤:
            _清除例外鏈(錯誤)
            if 連線 is not None:
                _清理連線(連線, 已開始 and not 已提交, 回滾控制, 關閉控制)
            self = 工作階段權杖 = CSRF權杖 = 動作 = 工作階段雜湊 = CSRF雜湊 = 現在時間 = None
            連線 = 資料列 = 角色清單 = 使用者 = 替代權杖 = 游標 = 結果 = None
            回滾控制.clear(); 關閉控制.clear(); _清除例外鏈(錯誤); 錯誤 = None
            raise
        except BaseException:
            失敗 = True
        if 連線 is not None:
            _清理連線(連線, 已開始 and not 已提交, 回滾控制, 關閉控制)
        連線 = None
        if 回滾控制 or 關閉控制:
            self = 工作階段權杖 = CSRF權杖 = 動作 = 工作階段雜湊 = CSRF雜湊 = 現在時間 = None
            資料列 = 角色清單 = 使用者 = 替代權杖 = 游標 = 結果 = None
            if 回滾控制:
                關閉控制.clear(); _重拋控制流程(回滾控制.pop())
            回滾控制.clear(); _重拋控制流程(關閉控制.pop())
        回滾控制.clear(); 關閉控制.clear()
        if 失敗 or (拒絕碼 is None and (not 已提交 or 結果 is None)):
            固定錯誤 = 網頁認證不可用
            固定訊息 = "auth_unavailable"
        elif 拒絕碼 == "csrf_invalid":
            固定錯誤 = 網頁CSRF無效
            固定訊息 = 拒絕碼
        elif 拒絕碼 == "unauthorized":
            固定錯誤 = 網頁未授權
            固定訊息 = 拒絕碼
        else:
            return 結果
        self = 工作階段權杖 = CSRF權杖 = 動作 = 工作階段雜湊 = CSRF雜湊 = 現在時間 = None
        資料列 = 角色清單 = 使用者 = 替代權杖 = 游標 = 結果 = None
        raise 固定錯誤(固定訊息) from None
