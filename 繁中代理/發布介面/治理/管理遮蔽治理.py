"""A20 管理遮蔽的exact authority、sealed outcomes與immutable receipt。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import Condition, RLock
from typing import Literal

from fastapi import HTTPException, Request, Response

from ..設定 import (
    網頁CSRFCookie名稱,
    網頁CSRFHeader名稱,
    網頁安全設定,
    網頁工作階段Cookie名稱,
    網頁工作階段Cookie路徑,
    網頁工作階段CookieSameSite,
)
from ..網頁工作階段 import (
    網頁CSRF無效,
    網頁管理權限不足,
    網頁使用者,
    網頁未授權,
    網頁工作階段服務,
    網頁認證不可用,
)
from .遮蔽 import SQLite不可逆遮蔽服務, 遮蔽目標內容無效, 遮蔽目標衝突, 遮蔽路徑無效
from .遮蔽命令 import SQLite遮蔽命令服務, 遮蔽命令冪等衝突, 遮蔽命令目標不存在

_控制流程 = (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)
_權杖字元 = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_目標類型 = Literal[
    "invocation_input", "metadata", "output", "error", "run_event",
    "tool_arguments", "tool_result", "tool_error",
]


@dataclass(frozen=True, slots=True)
class 管理遮蔽請求:
    """治理module唯一接受的已驗證mutation request。"""

    管理員識別碼: str
    冪等鍵: str
    端點識別碼: str
    呼叫識別碼: str
    目標類型: _目標類型
    目標列識別碼: str
    JSON路徑: str
    原因: str


@dataclass(frozen=True, slots=True)
class 管理遮蔽收據:
    """由durable transaction graph建立的完整不可變receipt。"""

    遮蔽識別碼: str
    呼叫識別碼: str
    目標類型: _目標類型
    目標列識別碼: str
    JSON路徑: str
    原值SHA256: str
    原因: str
    管理員識別碼: str
    稽核事件識別碼: str
    是墓碑: bool
    遮蔽時間: float

    def __getitem__(self, key: str):
        """只為既有command callers保留read-only英文字段索引。"""
        mapping = {
            "redaction_id": self.遮蔽識別碼,
            "invocation_id": self.呼叫識別碼,
            "target_type": self.目標類型,
            "target_row_id": self.目標列識別碼,
            "json_path": self.JSON路徑,
            "original_sha256": self.原值SHA256,
            "reason": self.原因,
            "actor_id": self.管理員識別碼,
            "audit_event_id": self.稽核事件識別碼,
            "is_tombstone": self.是墓碑,
            "redacted_at": self.遮蔽時間,
        }
        return mapping[key]


@dataclass(frozen=True, slots=True)
class 管理遮蔽成功:
    """唯一成功sealed outcome。"""

    收據: 管理遮蔽收據


@dataclass(frozen=True, slots=True)
class 管理遮蔽不存在:
    """transaction owner證實anti-enumeration not-found。"""


@dataclass(frozen=True, slots=True)
class 管理遮蔽冪等衝突:
    """transaction owner證實idempotency conflict。"""


@dataclass(frozen=True, slots=True)
class 管理遮蔽目標衝突:
    """transaction owner證實target/path conflict。"""


@dataclass(frozen=True, slots=True)
class 管理遮蔽驗證失敗:
    """transaction owner證明JSON Pointer無法定位既有值。"""


@dataclass(frozen=True, slots=True)
class 管理遮蔽內部失敗:
    """未安裝、draining、factory、schema、DB或receipt failure。"""


@dataclass(frozen=True, slots=True)
class 管理遮蔽授權:
    """restore與consume皆成功後重建的完整admin principal。"""

    使用者: 網頁使用者


class _安裝嘗試:
    """只由module-sealed installer建立的opaque attempt token。"""

    __slots__ = ("_權限身份", "_序號", "_封印", "_狀態", "_世代")

    def __init__(self, 權限身份: int, 序號: int, 封印: object) -> None:
        self._權限身份 = 權限身份
        self._序號 = 序號
        self._封印 = 封印
        self._狀態 = "new"
        self._世代: int | None = None

    def __copy__(self):
        raise TypeError("管理遮蔽治理安裝嘗試不可複製") from None

    def __deepcopy__(self, memo):
        del memo
        raise TypeError("管理遮蔽治理安裝嘗試不可複製") from None

    def __reduce__(self):
        raise TypeError("管理遮蔽治理安裝嘗試不可序列化") from None


class 管理遮蔽治理權限:
    """固定route interface、authority-first CSRF與generation-safe治理installation。"""

    __slots__ = (
        "_工作階段", "_設定", "_條件", "_世代", "_安裝", "_租用",
        "_排空世代", "_嘗試序號", "_嘗試封印", "_已準備", "_授權相依",
    )

    def __init__(self, 工作階段: 網頁工作階段服務, 設定: 網頁安全設定) -> None:
        if (
            type(工作階段) is not 網頁工作階段服務
            or type(設定) is not 網頁安全設定
            or 網頁工作階段服務.讀取有效秒數(工作階段) != 設定.工作階段有效秒數
        ):
            raise ValueError("管理遮蔽治理設定無效") from None
        self._工作階段 = 工作階段
        self._設定 = 設定
        self._條件 = Condition(RLock())
        self._世代 = 0
        self._安裝: tuple[int, SQLite不可逆遮蔽服務, SQLite遮蔽命令服務] | None = None
        self._租用 = 0
        self._排空世代: int | None = None
        self._嘗試序號 = 0
        self._嘗試封印 = object()
        self._已準備: tuple[int, object, SQLite不可逆遮蔽服務, SQLite遮蔽命令服務] | None = None

        def authority_first(請求: Request, 回應: Response) -> 管理遮蔽授權:
            return self._授權(請求, 回應)

        self._授權相依 = authority_first

    @property
    def 授權相依項(self):
        """返回route唯一module-owned CSRF dependency identity。"""
        return self._授權相依

    def 安裝(self, 服務: SQLite不可逆遮蔽服務, 命令服務: SQLite遮蔽命令服務) -> int:
        """安裝exact SQLite transaction graph並返回不可重用generation token。"""
        嘗試 = _管理遮蔽安裝能力.建立安裝嘗試(self)
        完成 = False
        try:
            _管理遮蔽安裝能力.準備安裝(self, 嘗試, 服務, 命令服務)
            世代 = _管理遮蔽安裝能力.發布已準備安裝(self, 嘗試)
            完成 = True
            return 世代
        finally:
            if not 完成:
                _管理遮蔽安裝能力.依安裝嘗試撤銷並等待(self, 嘗試)

    def _驗證安裝嘗試(self, 嘗試: object) -> _安裝嘗試:
        if (
            type(嘗試) is not _安裝嘗試
            or 嘗試._權限身份 != id(self)
            or 嘗試._封印 is not self._嘗試封印
            or type(嘗試._序號) is not int
            or 嘗試._序號 < 1
        ):
            raise ValueError("管理遮蔽治理安裝嘗試無效") from None
        return 嘗試

    def _建立安裝嘗試(self) -> _安裝嘗試:
        """建立尚未接觸service authority的opaque attempt。"""
        with self._條件:
            self._嘗試序號 += 1
            return _安裝嘗試(id(self), self._嘗試序號, self._嘗試封印)

    def _準備安裝(
        self, 嘗試: object, 服務: SQLite不可逆遮蔽服務, 命令服務: SQLite遮蔽命令服務,
    ) -> None:
        """將exact services綁定到尚未發布的attempt。"""
        嘗試 = self._驗證安裝嘗試(嘗試)
        if type(服務) is not SQLite不可逆遮蔽服務 or type(命令服務) is not SQLite遮蔽命令服務:
            raise ValueError("管理遮蔽治理安裝無效") from None
        with self._條件:
            if (
                嘗試._狀態 != "new" or self._已準備 is not None
                or self._安裝 is not None or self._排空世代 is not None
            ):
                raise ValueError("管理遮蔽治理安裝無效") from None
            self._已準備 = (嘗試._序號, 嘗試._封印, 服務, 命令服務)
            嘗試._狀態 = "bound"

    def _發布已準備安裝(self, 嘗試: object) -> int:
        """只發布同authority目前唯一的bound attempt。"""
        嘗試 = self._驗證安裝嘗試(嘗試)
        with self._條件:
            準備 = self._已準備
            if (
                嘗試._狀態 != "bound" or 準備 is None
                or 準備[:2] != (嘗試._序號, 嘗試._封印)
                or self._安裝 is not None or self._排空世代 is not None
            ):
                raise ValueError("管理遮蔽治理安裝無效") from None
            self._世代 += 1
            self._安裝 = (self._世代, 準備[2], 準備[3])
            self._已準備 = None
            嘗試._世代 = self._世代
            嘗試._狀態 = "published"
            return self._世代

    def _依安裝嘗試撤銷並等待(self, 嘗試: object) -> None:
        """收斂exact attempt的new、bound或published狀態；重複呼叫安全no-op。"""
        嘗試 = self._驗證安裝嘗試(嘗試)
        with self._條件:
            if 嘗試._狀態 == "new":
                嘗試._狀態 = "revoked"
                return
            if 嘗試._狀態 == "bound":
                if self._已準備 is not None and self._已準備[:2] == (嘗試._序號, 嘗試._封印):
                    self._已準備 = None
                嘗試._狀態 = "revoked"
                return
            if 嘗試._狀態 in ("revoked", "finished"):
                return
            世代 = 嘗試._世代
            if type(世代) is not int:
                raise ValueError("管理遮蔽治理安裝嘗試無效") from None
            if self._安裝 is not None and self._安裝[0] == 世代:
                self._排空世代 = 世代
                self._安裝 = None
            while self._排空世代 == 世代 and self._租用:
                self._條件.wait()
            if self._排空世代 == 世代:
                self._排空世代 = None
                self._條件.notify_all()
            嘗試._狀態 = "finished"

    def 解除(self, 世代: int) -> None:
        """依exact generation撤銷authority，並讓同generation callers共同等待active leases。"""
        if type(世代) is not int or 世代 < 1:
            raise ValueError("管理遮蔽治理解除無效") from None
        with self._條件:
            if self._安裝 is not None and self._安裝[0] == 世代:
                self._排空世代 = 世代
                self._安裝 = None
            elif self._排空世代 != 世代:
                return
            while self._租用:
                self._條件.wait()
            if self._排空世代 == 世代:
                self._排空世代 = None
                self._條件.notify_all()

    def 執行(self, 請求: 管理遮蔽請求):
        """只把exact transaction provenance封成route可映射的sealed outcome。"""
        if type(請求) is not 管理遮蔽請求:
            return 管理遮蔽內部失敗()
        with self._條件:
            安裝 = self._安裝
            if 安裝 is None or self._排空世代 is not None:
                return 管理遮蔽內部失敗()
            self._租用 += 1
        try:
            _, 服務, 命令服務 = 安裝
            try:
                收據 = SQLite不可逆遮蔽服務.執行命令(
                    服務,
                    命令服務,
                    管理員識別碼=請求.管理員識別碼,
                    冪等鍵=請求.冪等鍵,
                    端點識別碼=請求.端點識別碼,
                    呼叫識別碼=請求.呼叫識別碼,
                    目標類型=請求.目標類型,
                    目標列識別碼=請求.目標列識別碼,
                    JSON路徑=請求.JSON路徑,
                    原因=請求.原因,
                )
            except _控制流程:
                raise
            except 遮蔽命令冪等衝突:
                return 管理遮蔽冪等衝突()
            except 遮蔽命令目標不存在:
                return 管理遮蔽不存在()
            except 遮蔽目標衝突:
                return 管理遮蔽目標衝突()
            except 遮蔽路徑無效:
                return 管理遮蔽驗證失敗()
            except 遮蔽目標內容無效:
                return 管理遮蔽驗證失敗()
            except BaseException:
                return 管理遮蔽內部失敗()
            if type(收據) is not 管理遮蔽收據:
                return 管理遮蔽內部失敗()
            return 管理遮蔽成功(收據)
        finally:
            with self._條件:
                self._租用 -= 1
                if self._租用 == 0:
                    self._條件.notify_all()

    def _授權(self, 請求: Request, 回應: Response) -> 管理遮蔽授權:
        """raw-cookie唯一性後，以一次role-first transaction取得admin與successor。"""
        工作權杖 = _讀取cookie(請求, 網頁工作階段Cookie名稱)
        csrf標頭 = _讀取header(請求, 網頁CSRFHeader名稱)
        if 工作權杖 is None:
            raise HTTPException(401, {"code": "unauthorized"}) from None
        try:
            輪替 = self._工作階段.授權管理操作(工作權杖, csrf標頭)
        except _控制流程:
            raise
        except 網頁未授權:
            raise HTTPException(401, {"code": "unauthorized"}) from None
        except 網頁認證不可用:
            raise HTTPException(503, {"code": "auth_unavailable"}) from None
        except 網頁管理權限不足:
            raise HTTPException(403, {"code": "admin_required"}) from None
        except 網頁CSRF無效:
            raise HTTPException(403, {"code": "csrf_invalid"}) from None
        _設定successor(回應, 輪替, self._設定)
        return 管理遮蔽授權(輪替.使用者)


class _密封管理遮蔽安裝能力:
    """在module initialization捕捉原始primitives的不可替換installer seam。"""

    __slots__ = ("_建立", "_準備", "_發布", "_收斂", "_已密封")

    def __init__(self) -> None:
        object.__setattr__(self, "_建立", 管理遮蔽治理權限.__dict__["_建立安裝嘗試"])
        object.__setattr__(self, "_準備", 管理遮蔽治理權限.__dict__["_準備安裝"])
        object.__setattr__(self, "_發布", 管理遮蔽治理權限.__dict__["_發布已準備安裝"])
        object.__setattr__(self, "_收斂", 管理遮蔽治理權限.__dict__["_依安裝嘗試撤銷並等待"])
        object.__setattr__(self, "_已密封", True)

    def __setattr__(self, 名稱, 值) -> None:
        del 名稱, 值
        raise AttributeError("管理遮蔽安裝能力已密封") from None

    def __copy__(self):
        raise TypeError("管理遮蔽安裝能力不可複製") from None

    def __deepcopy__(self, memo):
        del memo
        raise TypeError("管理遮蔽安裝能力不可複製") from None

    def __reduce__(self):
        raise TypeError("管理遮蔽安裝能力不可序列化") from None

    def 建立安裝嘗試(self, 權限: 管理遮蔽治理權限) -> _安裝嘗試:
        if type(權限) is not 管理遮蔽治理權限:
            raise ValueError("管理遮蔽治理安裝嘗試無效") from None
        return self._建立(權限)

    def 準備安裝(
        self, 權限: 管理遮蔽治理權限, 嘗試: object,
        服務: SQLite不可逆遮蔽服務, 命令服務: SQLite遮蔽命令服務,
    ) -> None:
        if type(權限) is not 管理遮蔽治理權限:
            raise ValueError("管理遮蔽治理安裝嘗試無效") from None
        self._準備(權限, 嘗試, 服務, 命令服務)

    def 發布已準備安裝(self, 權限: 管理遮蔽治理權限, 嘗試: object) -> int:
        if type(權限) is not 管理遮蔽治理權限:
            raise ValueError("管理遮蔽治理安裝嘗試無效") from None
        return self._發布(權限, 嘗試)

    def 依安裝嘗試撤銷並等待(self, 權限: 管理遮蔽治理權限, 嘗試: object) -> None:
        if type(權限) is not 管理遮蔽治理權限:
            raise ValueError("管理遮蔽治理安裝嘗試無效") from None
        self._收斂(權限, 嘗試)


_管理遮蔽安裝能力 = _密封管理遮蔽安裝能力()


def 是管理遮蔽CSRF相依項(呼叫: object, 權限: object) -> bool:
    """以exact authority與dependency identity證明唯一module-owned CSRF gate。"""
    return type(權限) is 管理遮蔽治理權限 and 呼叫 is 權限.授權相依項


def _讀取權杖(值: object) -> str | None:
    if type(值) is not str or not 32 <= len(值) <= 512 or any(字元 not in _權杖字元 for 字元 in 值):
        return None
    return 值


def _讀取cookie(請求: Request, 名稱: str) -> str | None:
    """只接受raw Cookie fields中exact一個published session occurrence。"""
    出現: list[str] = []
    try:
        for 鍵, 值 in 請求.scope.get("headers", ()):
            if 鍵.lower() != b"cookie":
                continue
            for 片段 in 值.decode("latin-1").split(";"):
                左, 分隔, 右 = 片段.strip().partition("=")
                if 分隔 and 左 == 名稱:
                    出現.append(右)
        return _讀取權杖(出現[0]) if len(出現) == 1 else None
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return None


def _讀取header(請求: Request, 名稱: str) -> str | None:
    名稱位元 = 名稱.lower().encode("ascii")
    值 = [v for k, v in 請求.scope.get("headers", ()) if k.lower() == 名稱位元]
    if len(值) != 1 or len(值[0]) > 512:
        return None
    try:
        return _讀取權杖(值[0].decode("ascii"))
    except UnicodeDecodeError:
        return None


def _設定successor(回應: Response, 結果, 設定: 網頁安全設定) -> None:
    權杖 = 結果.CSRF權杖 or ""
    回應.headers[網頁CSRFHeader名稱] = 權杖
    回應.set_cookie(
        網頁CSRFCookie名稱,
        權杖,
        max_age=設定.工作階段有效秒數,
        expires=結果.到期時間,
        path=網頁工作階段Cookie路徑,
        secure=設定.Cookie安全,
        httponly=True,
        samesite=網頁工作階段CookieSameSite,
    )


def _successor標頭(回應: Response) -> dict[str, str]:
    標頭: dict[str, str] = {}
    if 回應.headers.get(網頁CSRFHeader名稱):
        標頭[網頁CSRFHeader名稱] = 回應.headers[網頁CSRFHeader名稱]
    cookies = 回應.headers.getlist("set-cookie")
    if cookies:
        標頭["set-cookie"] = cookies[-1]
    return 標頭
