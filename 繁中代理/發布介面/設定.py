"""發布介面 FastAPI 應用程式與 Web 安全固定設定。"""

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

發布介面標題 = "繁中代理發布介面"
"""OpenAPI 的固定應用程式標題。"""

發布介面版本 = "0.1.0"
"""OpenAPI 的固定應用程式版本。"""

允許路由前綴 = (
    "/api/published-endpoints",
    "/api/admin",
    "/api/chat",
    "/api/auth",
    "/v1/endpoints",
)
"""後續 invoke、管理與認證能力可使用的 exact router prefixes。"""

路由設定錯誤訊息 = "發布介面路由設定無效"
"""composition inventory 不合法時的固定錯誤。"""

啟動錯誤訊息 = "發布介面啟動失敗"
"""一般 startup 失敗的固定錯誤。"""

關閉錯誤訊息 = "發布介面關閉失敗"
"""一般 shutdown 失敗的固定錯誤。"""

網頁工作階段Cookie名稱 = "published_web_session"
網頁CSRFCookie名稱 = "published_web_csrf"
網頁工作階段Cookie路徑 = "/api"
網頁工作階段CookieSameSite = "lax"
網頁CSRFHeader名稱 = "X-CSRF-Token"
登入請求最大位元組 = 1024


class 限制登入請求Middleware:
    """在 FastAPI JSON parser 前只 materialize bounded 的完整 login body。"""

    def __init__(self, 應用):
        """保存下一層 ASGI 應用。"""
        self.應用 = 應用

    async def __call__(self, 範圍, 接收, 傳送):
        """只攔截 canonical login 並在轉交前限制完整 body 位元組。"""
        if 範圍.get("type") != "http" or 範圍.get("method") != "POST" or 範圍.get("path") != "/api/auth/login":
            await self.應用(範圍, 接收, 傳送)
            return
        訊息清單 = []
        總量 = 0
        while True:
            訊息 = await 接收()
            內容 = 訊息.get("body", b"")
            總量 += len(內容)
            if 總量 > 登入請求最大位元組:
                回應 = JSONResponse({"detail": {"code": "request_invalid"}}, status_code=422)
                await 回應(範圍, 接收, 傳送)
                return
            訊息清單.append(訊息)
            if not 訊息.get("more_body", False):
                break

        async def 接收bounded內容():
            """依原始 ASGI message 邊界重播已受限的 body。"""
            if 訊息清單:
                return 訊息清單.pop(0)
            return {"type": "http.disconnect"}

        await self.應用(範圍, 接收bounded內容, 傳送)


@dataclass(frozen=True, slots=True)
class 網頁安全設定:
    """不可變的 exact-origin CORS 與 cookie 設定。"""

    允許來源: tuple[str, ...] = ()
    Cookie安全: bool = True
    工作階段有效秒數: int = 86_400

    def __post_init__(self) -> None:
        """只接受 HTTPS 或明確 loopback HTTP serialized origins。"""
        if (
            type(self.允許來源) is not tuple
            or type(self.Cookie安全) is not bool
            or type(self.工作階段有效秒數) is not int
            or not 60 <= self.工作階段有效秒數 <= 604_800
        ):
            raise ValueError("Web安全設定無效")
        已見: set[str] = set()
        全為不安全loopback = bool(self.允許來源)
        for 來源 in self.允許來源:
            if type(來源) is not str or 來源 in 已見 or not _合法來源(來源):
                raise ValueError("Web安全設定無效")
            已見.add(來源)
            全為不安全loopback &= 來源.startswith("http://") and _是loopback(來源)
        if not self.Cookie安全 and not 全為不安全loopback:
            raise ValueError("Web安全設定無效")


def _是loopback(來源: str) -> bool:
    """判斷 hostname 是否為 localhost 或 IP loopback。"""
    主機 = urlsplit(來源).hostname
    if 主機 == "localhost":
        return True
    try:
        return ipaddress.ip_address(主機 or "").is_loopback
    except ValueError:
        return False


def _合法來源(來源: str) -> bool:
    """拒絕 wildcard/null/credential/path/query/fragment/control。"""
    if not 來源 or 來源 == "null" or "*" in 來源 or any(ord(字元) < 32 for 字元 in 來源):
        return False
    try:
        拆解 = urlsplit(來源)
        _ = 拆解.port
    except ValueError:
        return False
    return (
        拆解.scheme in ("http", "https")
        and 拆解.hostname is not None
        and 拆解.username is None
        and 拆解.password is None
        and 拆解.path == ""
        and 拆解.query == ""
        and 拆解.fragment == ""
        and (拆解.scheme == "https" or _是loopback(來源))
    )


def 套用網頁CORS(應用程式: FastAPI, 設定: 網頁安全設定) -> None:
    """套用 credentialed exact allowlist，不允許 wildcard。"""
    if type(應用程式) is not FastAPI or type(設定) is not 網頁安全設定:
        raise ValueError("Web安全設定無效")
    應用程式.add_middleware(
        CORSMiddleware,
        allow_origins=list(設定.允許來源),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", 網頁CSRFHeader名稱],
        expose_headers=[網頁CSRFHeader名稱],
        max_age=600,
    )
