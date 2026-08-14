"""Production-built SPA的有界snapshot、路由與lifespan composition。"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Mapping, cast

from fastapi import APIRouter, Path as 路徑欄位, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from .相依項 import 發布介面相依項
from .設定 import (
    ProductionSPA根操作識別碼,
    ProductionSPA操作識別碼,
    ProductionSPA路由方法,
)


_固定設定錯誤 = "Production SPA設定無效"
_固定不可取得 = {"detail": {"message": "網頁應用程式不可取得"}}
_固定不存在 = {"detail": "Not Found"}
_固定方法不允許 = {"detail": "Method Not Allowed"}
_最大檔案數 = 1024
_最大總位元組 = 32 * 1024 * 1024
_最大單檔位元組 = 8 * 1024 * 1024
_最大HTML位元組 = 1024 * 1024
_雜湊檔名 = re.compile(r"^[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:css|js|mjs|png|jpg|jpeg|gif|svg|webp|ico|woff|woff2)$")
_資源參照 = re.compile(r'(?:src|href)="(/assets/[A-Za-z0-9_./-]+)"')
_媒體類型 = MappingProxyType({
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
})
_安全標頭 = MappingProxyType({
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
})


@dataclass(frozen=True, slots=True)
class ProductionSPA設定:
    """只保存lexical absolute dist authority，filesystem验证延至startup。"""

    Dist根: Path

    def __post_init__(self) -> None:
        """拒絕relative、空basename與显式父层穿越，不執行filesystem I/O。"""
        if (
            not isinstance(self.Dist根, Path)
            or not self.Dist根.is_absolute()
            or not self.Dist根.name
            or ".." in self.Dist根.parts
        ):
            raise ValueError(_固定設定錯誤) from None


@dataclass(frozen=True, slots=True)
class _資源內容:
    """保存immutable bytes与已验证media type。"""

    內容: bytes
    媒體類型: str


@dataclass(frozen=True, slots=True)
class _SPA快照:
    """保存单一startup generation的完整immutable artifact。"""

    HTML: bytes
    資源: Mapping[str, _資源內容]


class _SPA狀態:
    """让router只观察当前lifespan generation，不持有filesystem path。"""

    __slots__ = ("_快照",)

    def __init__(self) -> None:
        """建立尚未安装artifact的request-visible状态。"""
        self._快照: _SPA快照 | None = None

    def 安裝(self, 快照: _SPA快照, /) -> None:
        """只安装单一lifespan generation的exact快照。"""
        if type(快照) is not _SPA快照 or self._快照 is not None:
            raise RuntimeError
        self._快照 = 快照

    def 讀取(self) -> _SPA快照 | None:
        """回传当前generation；startup前与shutdown后为None。"""
        return self._快照

    def 清除(self, 快照: _SPA快照, /) -> None:
        """只允许拥有当前generation的resource清除authority。"""
        if self._快照 is not 快照:
            raise RuntimeError
        self._快照 = None


class _SPA資源:
    """由既有application lifespan独占并清除单一snapshot。"""

    __slots__ = ("_狀態", "_快照")

    def __init__(self, 狀態: _SPA狀態, 快照: _SPA快照) -> None:
        """捕捉单一状态与generation identity供exact-once shutdown。"""
        self._狀態 = 狀態
        self._快照 = 快照

    async def 關閉(self) -> None:
        """关闭generation并释放所有artifact bytes引用。"""
        狀態, 快照 = self._狀態, self._快照
        if type(狀態) is not _SPA狀態 or type(快照) is not _SPA快照:
            raise RuntimeError
        self._狀態 = self._快照 = None  # type: ignore[assignment]
        狀態.清除(快照)


class 拒絕ProductionSPA未知方法Middleware:
    """在route-policy外的HTTP method進入router前固定回JSON 404。

    描述：只建立reject-only method boundary，不提供SPA內容、不讀取request body。
    參數：``app`` 是canonical ASGI應用程式。
    返回值：ASGI呼叫無返回值；核准method轉交內層，其他method直接送出404。
    """

    def __init__(self, 應用) -> None:
        """保存canonical ASGI應用。

        描述：建立單一reject-only method boundary。
        參數：``應用`` 是下一層canonical ASGI callable。
        返回值：建構完成後不返回值。
        """
        self.應用 = 應用

    async def __call__(self, 範圍, 接收, 傳送) -> None:
        """拒絕route-policy外method或轉交下一層。

        描述：只檢查HTTP method，不讀request body或提供SPA內容。
        參數：``範圍``、``接收``、``傳送``是標準ASGI介面值。
        返回值：完成固定404或內層ASGI呼叫後不返回值。
        """
        if 範圍.get("type") == "http" and 範圍.get("method") not in ProductionSPA路由方法:
            允許方法 = _讀取Backend部分匹配方法(範圍.get("app"), 範圍)
            if 允許方法:
                回應 = JSONResponse(
                    _固定方法不允許, status_code=405,
                    headers={**_安全標頭, "Allow": ", ".join(允許方法)},
                )
            else:
                回應 = JSONResponse(_固定不存在, status_code=404, headers=dict(_安全標頭))
            await 回應(範圍, 接收, 傳送)
            return
        await self.應用(範圍, 接收, 傳送)


def 建立ProductionSPA相依項(設定: ProductionSPA設定) -> 發布介面相依項:
    """建立一个root fallback router与一个startup snapshot resource factory。"""
    if type(設定) is not ProductionSPA設定:
        raise ValueError(_固定設定錯誤) from None
    狀態 = _SPA狀態()
    dist根 = 設定.Dist根
    路由器 = APIRouter()

    def 建立回應(請求: Request, 前端路徑: str) -> Response:
        """只从当前immutable snapshot建立asset、fallback或固定错误。"""
        快照 = 狀態.讀取()
        if 快照 is None:
            return JSONResponse(_固定不可取得, status_code=503, headers=dict(_安全標頭))
        允許方法 = _讀取Backend部分匹配方法(請求.app, 請求.scope)
        if 允許方法:
            return JSONResponse(
                _固定方法不允許, status_code=405,
                headers={**_安全標頭, "Allow": ", ".join(允許方法)},
            )
        if 請求.method not in {"GET", "HEAD"}:
            return JSONResponse(_固定不存在, status_code=404, headers=dict(_安全標頭))
        if 前端路徑 == "assets" or 前端路徑.startswith("assets/"):
            資源 = 快照.資源.get("/" + 前端路徑)
            if 資源 is None:
                return JSONResponse(_固定不存在, status_code=404, headers=dict(_安全標頭))
            return Response(
                資源.內容, media_type=資源.媒體類型,
                headers={**_安全標頭, "Cache-Control": "public, max-age=31536000, immutable"},
            )
        if 前端路徑 == "api" or 前端路徑.startswith("api/") or 前端路徑 == "v1" or 前端路徑.startswith("v1/"):
            return JSONResponse(_固定不存在, status_code=404, headers=dict(_安全標頭))
        return Response(
            快照.HTML, media_type="text/html; charset=utf-8",
            headers={**_安全標頭, "Cache-Control": "no-store"},
        )

    @路由器.api_route(
        "/", methods=sorted(ProductionSPA路由方法), include_in_schema=False,
        operation_id="production_spa_root_read",
    )
    async def 讀取ProductionSPA根(請求: Request) -> Response:
        """提供明确root HTML并保持与fallback相同policy。"""
        return 建立回應(請求, "")

    @路由器.api_route(
        "/{frontend_path:path}", methods=sorted(ProductionSPA路由方法), include_in_schema=False,
        operation_id="production_spa_fallback_read",
    )
    async def 讀取ProductionSPA(
        請求: Request,
        前端路徑: str = 路徑欄位(alias="frontend_path"),
    ) -> Response:
        """Backend未匹配後，僅對GET/HEAD提供bounded asset或SPA fallback。"""
        return 建立回應(請求, 前端路徑)

    async def 建立資源() -> _SPA資源:
        """在任何request前读取、验证并发布单一immutable dist snapshot。"""
        快照 = _建立快照(dist根)
        狀態.安裝(快照)
        return _SPA資源(狀態, 快照)

    return 發布介面相依項((路由器,), (建立資源,))


def _建立快照(dist根: Path) -> _SPA快照:
    """拒绝alias、损坏、超限与未hash资源，再复制为immutable bytes。"""
    Dist描述器 = Assets描述器 = None
    try:
        Dist描述器, Dist身份 = _開啟目錄(dist根)
        Assets描述器, Assets身份 = _開啟目錄("assets", 目錄描述器=Dist描述器)
        HTML = _讀取檔案("index.html", _最大HTML位元組, 目錄描述器=Dist描述器)
        HTML文字 = HTML.decode("utf-8", errors="strict")
        if (
            '<div id="root"></div>' not in HTML文字
            or "/src/" in HTML文字
            or '<script type="module"' not in HTML文字
        ):
            raise ValueError
        資源字典: dict[str, _資源內容] = {}
        總位元組 = len(HTML)
        with os.scandir(Assets描述器) as 項目列:
            for 項目 in 項目列:
                if 項目.is_symlink() or not 項目.is_file(follow_symlinks=False):
                    raise ValueError
                名稱 = 項目.name
                if type(名稱) is not str or "/" in 名稱 or _雜湊檔名.fullmatch(名稱) is None:
                    raise ValueError
                內容 = _讀取檔案(名稱, _最大單檔位元組, 目錄描述器=Assets描述器)
                總位元組 += len(內容)
                if len(資源字典) >= _最大檔案數 or 總位元組 > _最大總位元組:
                    raise ValueError
                媒體類型 = _媒體類型.get(Path(名稱).suffix.lower())
                if type(媒體類型) is not str:
                    raise ValueError
                資源字典["/assets/" + 名稱] = _資源內容(內容, 媒體類型)
        參照 = tuple(_資源參照.findall(HTML文字))
        if not 參照 or not any(路徑.endswith((".js", ".mjs")) for 路徑 in 參照):
            raise ValueError
        if any(路徑 not in 資源字典 for 路徑 in 參照):
            raise ValueError
        if _描述器身份(Assets描述器) != Assets身份 or _描述器身份(Dist描述器) != Dist身份:
            raise ValueError
        return _SPA快照(HTML, MappingProxyType(dict(資源字典)))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise ValueError(_固定設定錯誤) from None
    finally:
        if Assets描述器 is not None:
            os.close(Assets描述器)
        if Dist描述器 is not None:
            os.close(Dist描述器)


def _讀取檔案(名稱: str, 上限: int, *, 目錄描述器: int) -> bytes:
    """以nofollow descriptor固定单一regular inode并拒绝增长、替换或超限。"""
    描述器 = None
    內容區塊: list[bytes] = []
    try:
        不跟隨旗標 = getattr(os, "O_NOFOLLOW", None)
        if type(不跟隨旗標) is not int:
            raise ValueError
        開啟旗標 = os.O_RDONLY | 不跟隨旗標 | getattr(os, "O_CLOEXEC", 0)
        描述器 = os.open(名稱, 開啟旗標, dir_fd=目錄描述器)
        前 = os.fstat(描述器)
        if not stat.S_ISREG(前.st_mode) or not 0 < 前.st_size <= 上限:
            raise ValueError
        剩餘 = 前.st_size
        while 剩餘:
            區塊 = os.read(描述器, min(65_536, 剩餘))
            if not 區塊:
                raise ValueError
            內容區塊.append(區塊)
            剩餘 -= len(區塊)
        if os.read(描述器, 1):
            raise ValueError
        後 = os.fstat(描述器)
        身份欄位 = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(前, 欄位) != getattr(後, 欄位) for 欄位 in 身份欄位):
            raise ValueError
        return b"".join(內容區塊)
    except OSError:
        raise ValueError from None
    finally:
        內容區塊.clear()
        if 描述器 is not None:
            os.close(描述器)


def _開啟目錄(路徑: Path | str, *, 目錄描述器: int | None = None) -> tuple[int, tuple[int, ...]]:
    """nofollow開啟並pin住目錄inode；可相對既有descriptor開啟child。"""
    不跟隨旗標 = getattr(os, "O_NOFOLLOW", None)
    目錄旗標 = getattr(os, "O_DIRECTORY", None)
    if type(不跟隨旗標) is not int or type(目錄旗標) is not int:
        raise ValueError
    描述器 = os.open(
        os.fspath(路徑), os.O_RDONLY | 不跟隨旗標 | 目錄旗標 | getattr(os, "O_CLOEXEC", 0),
        dir_fd=目錄描述器,
    )
    try:
        return 描述器, _描述器身份(描述器)
    except BaseException:
        os.close(描述器)
        raise


def _描述器身份(描述器: int) -> tuple[int, ...]:
    """讀取已pin descriptor的目錄identity與mutation metadata。"""
    狀態 = os.fstat(描述器)
    if not stat.S_ISDIR(狀態.st_mode):
        raise ValueError
    return (
        狀態.st_dev, 狀態.st_ino, 狀態.st_mode, 狀態.st_mtime_ns, 狀態.st_ctime_ns,
    )


def _讀取Backend部分匹配方法(應用, 範圍) -> tuple[str, ...]:
    """讀取canonical backend inventory的partial match；返回允許method tuple。"""
    方法: set[str] = set()
    路由器 = getattr(應用, "router", None)
    路由清單 = getattr(路由器, "routes", ())
    for 路由 in 路由清單:
        if getattr(路由, "operation_id", None) in {
            ProductionSPA根操作識別碼, ProductionSPA操作識別碼,
        }:
            continue
        比對, _子Scope = 路由.matches(範圍)
        if 比對 is Match.PARTIAL:
            路由方法 = getattr(路由, "methods", None)
            if type(路由方法) in (set, frozenset):
                方法集合 = cast(AbstractSet[object], 路由方法)
                方法.update(值 for 值 in 方法集合 if type(值) is str)
    return tuple(sorted(方法))


__all__ = (
    "ProductionSPA設定", "建立ProductionSPA相依項", "拒絕ProductionSPA未知方法Middleware",
)
