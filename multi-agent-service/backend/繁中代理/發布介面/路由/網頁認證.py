"""W102 相容的 Web 帳密、cookie session 與 CSRF HTTP 邊界。"""
from __future__ import annotations
import json
import sqlite3
import secrets
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, StrictStr, create_model, constr
from 繁中代理.使用者 import 產生密碼雜湊, 驗證密碼雜湊
from ..設定 import (
    網頁CSRFCookie名稱, 網頁CSRFHeader名稱, 網頁安全設定,
    網頁工作階段CookieSameSite, 網頁工作階段Cookie名稱, 網頁工作階段Cookie路徑,
)
from ..網頁工作階段 import (
    網頁CSRF無效, 網頁使用者, 網頁未授權, 網頁工作階段服務, 網頁工作階段結果, 網頁認證不可用,
)
_密封工作階段服務: weakref.WeakSet = weakref.WeakSet()
登入請求模型 = create_model(
    "LoginRequest", __config__=ConfigDict(extra="forbid", strict=True),
    使用者名稱=(constr(strict=True, min_length=1, max_length=128), Field(alias="username")),
    密碼=(constr(strict=True, min_length=1, max_length=256), Field(alias="password")),
)
認證使用者模型 = create_model(
    "AuthUser", __config__=ConfigDict(extra="forbid", strict=True),
    識別碼=(constr(strict=True, min_length=1, max_length=128), Field(alias="id")),
    使用者名稱=(constr(strict=True, min_length=1, max_length=128), Field(alias="username")),
    角色=(constr(strict=True, min_length=1, max_length=64), Field(alias="role")),
)
認證工作階段回應模型 = create_model(
    "AuthSessionResponse", __config__=ConfigDict(extra="forbid", strict=True),
    使用者=(認證使用者模型, Field(alias="user")),
    CSRF權杖=(constr(strict=True, min_length=32, max_length=512), Field(alias="csrf_token")),
)
帳密驗證器 = Callable[[str, str], 網頁使用者]

class 網頁工作階段協定(Protocol):
    """Sealed session consumer surface shared by SQLite and PostgreSQL."""
    def 讀取有效秒數(self) -> int: ...
    def 發行(self, 使用者: 網頁使用者, 舊工作階段權杖: str | None = None, 使用者代理: str | None = None) -> 網頁工作階段結果: ...
    def 恢復(self, 工作階段權杖: str, csrf_token: str | None = None) -> 網頁工作階段結果: ...
    def 輪替(self, 工作階段權杖: str, csrf_token: str) -> 網頁工作階段結果: ...
    def 撤銷(self, 工作階段權杖: str, csrf_token: str) -> None: ...

def _是工作階段服務(服務: object) -> bool:
    """只接受既有 SQLite authority 或由 canonical factory 密封的 exact PostgreSQL authority。"""
    try:
        if type(服務) is 網頁工作階段服務:
            return type(網頁工作階段服務.讀取有效秒數(服務)) is int
        from ..PostgreSQL網頁工作階段 import PostgreSQL網頁工作階段服務
        return (
            type(服務) is PostgreSQL網頁工作階段服務
            and 服務 in _密封工作階段服務
            and type(PostgreSQL網頁工作階段服務.讀取有效秒數(服務)) is int
        )
    except BaseException:
        return False


def _讀取核准工作階段TTL(服務: object) -> int:
    """以 class-bound authority 讀取 TTL，拒絕 instance shadow。"""
    if type(服務) is 網頁工作階段服務:
        return 網頁工作階段服務.讀取有效秒數(服務)
    from ..PostgreSQL網頁工作階段 import PostgreSQL網頁工作階段服務
    if type(服務) is PostgreSQL網頁工作階段服務 and 服務 in _密封工作階段服務:
        return PostgreSQL網頁工作階段服務.讀取有效秒數(服務)
    raise ValueError("Web認證設定無效")


def 登錄核准工作階段服務(服務: object) -> None:
    """只讓 canonical 組裝登錄 exact PostgreSQL session authority。"""
    from ..PostgreSQL網頁工作階段 import PostgreSQL網頁工作階段服務
    if type(服務) is 網頁工作階段服務:
        return
    if type(服務) is not PostgreSQL網頁工作階段服務:
        raise ValueError("Web工作階段服務未獲核准")
    _密封工作階段服務.add(服務)

是核准工作階段服務 = _是工作階段服務

def 建立PostgreSQL帳密驗證器(設定) -> 帳密驗證器:
    """Repository-backed verifier with dummy PBKDF2 work for unknown users."""
    from 繁中代理.交易儲存設定 import 交易儲存設定
    if type(設定) is not 交易儲存設定 or 設定.後端 != "postgres":
        raise ValueError("PostgreSQL帳密驗證器設定無效")
    from 繁中代理.PostgreSQL使用者庫 import PostgreSQL使用者庫
    dummy = 產生密碼雜湊(secrets.token_urlsafe(32))
    users = PostgreSQL使用者庫(設定)
    def 驗證(使用者名稱: str, 密碼: str) -> 網頁使用者:
        row = users.讀取使用者(username=使用者名稱)
        saved = dummy if row is None else row.get("password_hash")
        valid = type(saved) is str and 驗證密碼雜湊(密碼, saved)
        if row is None or not valid or row.get("disabled") not in (False, 0):
            raise ValueError("invalid_credentials")
        roles = row.get("roles_json", row.get("roles", "[]"))
        if isinstance(roles, str):
            roles = json.loads(roles)
        return 網頁使用者(str(row["id"]), str(row["username"]), "admin" if "admin" in roles else "member")
    return 驗證
_TOKEN字元 = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_目前工作階段相依項清單: weakref.WeakSet = weakref.WeakSet()
_CSRF相依項清單: weakref.WeakSet = weakref.WeakSet()
_認證路由器TTL: dict[int, tuple[weakref.ReferenceType, int]] = {}
_CSRF接續狀態名稱 = "_testagent2_published_csrf_successor_headers"
_COOKIE參數 = lambda 名稱, 必要: {
    "name": 名稱, "in": "cookie", "required": 必要,
    "schema": {"type": "string", "minLength": 32, "maxLength": 512},
}
_HEADER參數 = {
    "name": 網頁CSRFHeader名稱, "in": "header", "required": True,
    "schema": {"type": "string", "minLength": 32, "maxLength": 512},
}
_SET_COOKIE文件 = {
    "Set-Cookie": {"description": "Web session/CSRF cookie issuance or deletion", "schema": {"type": "string"}},
}
_SUCCESSOR文件 = {
    **_SET_COOKIE文件,
    網頁CSRFHeader名稱: {"description": "Successor single-use CSRF token when rotated", "schema": {"type": "string"}},
}


class 目前工作階段HTTP錯誤(HTTPException):
    """讓 app handler 保留 current-session 固定錯誤與雙 cookie 清除。"""


def _讀取權杖(值: object) -> str | None:
    """只接受 bounded URL-safe exact token，避免解析器或雜湊接觸巨大輸入。"""
    if type(值) is not str or not 32 <= len(值) <= 512:
        return None
    if any(字元 not in _TOKEN字元 for 字元 in 值):
        return None
    return 值


def _讀取cookie(請求: Request, 名稱: str) -> str | None:
    """只由raw ASGI Cookie headers讀取唯一同名值，拒絕framework mapping折疊。"""
    return _讀取raw_cookie(請求, 名稱)[1]


def _讀取raw_cookie(請求: Request, 名稱: str) -> tuple[int, str | None]:
    """回傳目標出現狀態與唯一合法值：0缺少、1單值、2含糊而必須拒絕。"""
    try:
        預期名稱 = 名稱.encode("ascii")
        原始標頭 = 請求.scope.get("headers", ())
    except (AttributeError, TypeError, UnicodeEncodeError):
        return 2, None
    if type(原始標頭) not in (list, tuple):
        return 2, None
    命中值: bytes | None = None
    命中數 = 0
    cookie總位元組 = 0
    for 項目 in 原始標頭:
        if type(項目) is not tuple or len(項目) != 2:
            return 2, None
        鍵, 原始值 = 項目
        if type(鍵) is not bytes or type(原始值) is not bytes:
            return 2, None
        if 鍵.lower() != b"cookie":
            continue
        cookie總位元組 += len(原始值)
        if cookie總位元組 > 16_384:
            return 2, None
        for 片段 in 原始值.split(b";"):
            配對 = 片段.strip(b" \t")
            if b"=" not in 配對:
                continue
            餅乾名稱, 餅乾值 = 配對.split(b"=", 1)
            if 餅乾名稱 != 預期名稱:
                continue
            命中數 += 1
            if 命中數 != 1:
                return 2, None
            命中值 = 餅乾值
    if 命中數 == 0:
        return 0, None
    try:
        return 1, _讀取權杖(命中值.decode("ascii"))
    except (UnicodeDecodeError, AttributeError):
        return 1, None


def _讀取header(請求: Request, 名稱: str) -> str | None:
    """只接受一個 bounded exact header，重複或無法解碼皆拒絕。"""
    預期名稱 = 名稱.lower().encode("ascii")
    值清單 = [值 for 鍵, 值 in 請求.scope.get("headers", ()) if 鍵.lower() == 預期名稱]
    if len(值清單) != 1 or len(值清單[0]) > 512:
        return None
    try:
        return _讀取權杖(值清單[0].decode("ascii"))
    except UnicodeDecodeError:
        return None


def 建立SQLite帳密驗證器(資料庫路徑: str | Path) -> 帳密驗證器:
    """建立重用既有 PBKDF2 格式且 unknown user 仍做 dummy verify 的 adapter。"""
    路徑 = Path(資料庫路徑)
    虛擬雜湊 = 產生密碼雜湊(secrets.token_urlsafe(32))
    def 驗證(使用者名稱: str, 密碼: str) -> 網頁使用者:
        """將 credential failure 固定為 ValueError，infra/malformed roles 分開。"""
        連線 = None
        資料列 = None
        無效 = False
        不可用 = False
        使用者 = None
        try:
            連線 = sqlite3.connect(路徑, timeout=1)
            連線.row_factory = sqlite3.Row
            資料列 = 連線.execute(
                "SELECT id,username,password_hash,roles_json,disabled FROM users WHERE username=?",
                (使用者名稱,),
            ).fetchone()
            保存雜湊 = 虛擬雜湊 if 資料列 is None or type(資料列["password_hash"]) is not str else 資料列["password_hash"]
            有效 = 驗證密碼雜湊(密碼, 保存雜湊)
            無效 = 資料列 is None or not 有效 or type(資料列["disabled"]) is not int or 資料列["disabled"] != 0
            if not 無效:
                角色清單 = json.loads(資料列["roles_json"])
                if type(角色清單) is not list or any(type(角色值) is not str for 角色值 in 角色清單):
                    不可用 = True
                else:
                    使用者 = 網頁使用者(
                        str(資料列["id"]), str(資料列["username"]),
                        "admin" if "admin" in 角色清單 else "member",
                    )
        except (sqlite3.Error, TypeError, json.JSONDecodeError, ValueError):
            不可用 = True
        finally:
            if 連線 is not None:
                try:
                    連線.close()
                except sqlite3.Error:
                    不可用 = True
            使用者名稱 = 密碼 = 保存雜湊 = None
            資料列 = 連線 = None
        if 不可用:
            raise 網頁認證不可用("auth_unavailable") from None
        if 無效 or 使用者 is None:
            raise ValueError("invalid_credentials") from None
        return 使用者
    return 驗證


def _設定cookie(
    回應: Response, 名稱: str, 值: str, 結果: 網頁工作階段結果, 設定: 網頁安全設定,
) -> None:
    """以 DB absolute expiry 與 exact scope 寫 HttpOnly cookie。"""
    回應.set_cookie(
        名稱, 值, max_age=設定.工作階段有效秒數,
        expires=datetime.fromtimestamp(結果.到期時間, timezone.utc),
        path=網頁工作階段Cookie路徑, secure=設定.Cookie安全,
        httponly=True, samesite=網頁工作階段CookieSameSite,
    )


def _清除cookie(回應: Response, 設定: 網頁安全設定) -> None:
    """以 issuance 同 scope 刪除兩個 cookie。"""
    for 名稱 in (網頁工作階段Cookie名稱, 網頁CSRFCookie名稱):
        回應.delete_cookie(
            名稱, path=網頁工作階段Cookie路徑, secure=設定.Cookie安全,
            httponly=True, samesite=網頁工作階段CookieSameSite,
        )


def _錯誤(狀態碼: int, 錯誤碼: str, 設定: 網頁安全設定 | None = None) -> JSONResponse:
    """建立固定 detail error；logout failure 也清除 cookies。"""
    回應 = JSONResponse({"detail": {"code": 錯誤碼}}, status_code=狀態碼)
    if 設定 is not None:
        _清除cookie(回應, 設定)
    return 回應


def _建立DTO(結果: 網頁工作階段結果) -> dict[str, object]:
    """只回傳 W102 exact keys，role 只供 UI。"""
    return {
        "user": {
            "id": 結果.使用者.識別碼,
            "username": 結果.使用者.使用者名稱,
            "role": 結果.使用者.角色,
        },
        "csrf_token": 結果.CSRF權杖,
    }


def _建立目前工作階段DTO(使用者: 網頁使用者, CSRF權杖: str) -> dict[str, object]:
    """由 canonical principal 與 bounded transport successor 建立 exact A02 DTO。"""
    return {
        "user": {"id": 使用者.識別碼, "username": 使用者.使用者名稱, "role": 使用者.角色},
        "csrf_token": CSRF權杖,
    }


def 建立目前工作階段相依項(服務: 網頁工作階段服務, 設定: 網頁安全設定):
    """建立 A03 可重用、但只回最小 A02 principal 的 current-session hook。"""
    if (
        not _是工作階段服務(服務)
        or type(設定) is not 網頁安全設定
        or _讀取核准工作階段TTL(服務) != 設定.工作階段有效秒數
    ):
        raise ValueError("Web認證設定無效")

    def 取得目前工作階段(請求: Request, 回應: Response) -> 網頁使用者:
        """手動 bounded cookie recovery；authoritative restore exact-once。"""
        工作階段權杖 = _讀取cookie(請求, 網頁工作階段Cookie名稱)
        CSRF餅乾 = _讀取cookie(請求, 網頁CSRFCookie名稱)
        if 工作階段權杖 is None:
            raise 目前工作階段HTTP錯誤(401, {"code": "unauthorized"})
        try:
            結果 = 服務.恢復(工作階段權杖, CSRF餅乾)
        except 網頁未授權:
            raise 目前工作階段HTTP錯誤(401, {"code": "unauthorized"}) from None
        except 網頁認證不可用:
            raise 目前工作階段HTTP錯誤(503, {"code": "auth_unavailable"}) from None
        if 結果.csrf已輪替:
            回應.headers[網頁CSRFHeader名稱] = 結果.CSRF權杖 or ""
            _設定cookie(回應, 網頁CSRFCookie名稱, 結果.CSRF權杖 or "", 結果, 設定)
        return 結果.使用者

    _目前工作階段相依項清單.add(取得目前工作階段)
    return 取得目前工作階段


def 是模組目前工作階段相依項(呼叫: object) -> bool:
    """只承認本模組factory實際建立且仍由route持有的current-session identity。"""
    return 呼叫 in _目前工作階段相依項清單


def 讀取網頁認證路由器TTL(路由器: APIRouter) -> int | None:
    """供 application preflight 讀取 factory-sealed authoritative TTL。"""
    if type(路由器) is not APIRouter:
        return None
    項目 = _認證路由器TTL.get(id(路由器))
    if 項目 is None or 項目[0]() is not 路由器:
        return None
    return 項目[1]


def _登錄網頁認證路由器TTL(路由器: APIRouter, 有效秒數: int) -> None:
    """以 weak identity 保存 immutable TTL，避免 APIRouter unhashable equality。"""
    身份 = id(路由器)
    def 移除(參照):
        """只移除仍對應同一 weak reference 的 TTL 項目。"""
        項目 = _認證路由器TTL.get(身份)
        if 項目 is not None and 項目[0] is 參照:
            _認證路由器TTL.pop(身份, None)
    _認證路由器TTL[身份] = (weakref.ref(路由器, 移除), 有效秒數)


def 建立CSRF相依項(服務: 網頁工作階段服務, 設定: 網頁安全設定):
    """建立可供 browser mutation 共用的 single-use rotating dependency。"""
    if not _是工作階段服務(服務) or type(設定) is not 網頁安全設定 or _讀取核准工作階段TTL(服務) != 設定.工作階段有效秒數:
        raise ValueError("Web認證設定無效")
    def 驗證並輪替(
        請求: Request,
        回應: Response,
    ) -> 網頁使用者:
        """session failure 401；CSRF failure 403；成功傳 successor header/cookie。"""
        工作階段權杖 = _讀取cookie(請求, 網頁工作階段Cookie名稱)
        CSRF權杖 = _讀取header(請求, 網頁CSRFHeader名稱)
        if 工作階段權杖 is None:
            from fastapi import HTTPException
            raise HTTPException(401, {"code": "unauthorized"})
        try:
            結果 = 服務.輪替(工作階段權杖, CSRF權杖)
        except 網頁未授權:
            from fastapi import HTTPException
            raise HTTPException(401, {"code": "unauthorized"}) from None
        except 網頁CSRF無效:
            from fastapi import HTTPException
            raise HTTPException(403, {"code": "csrf_invalid"}) from None
        except 網頁認證不可用:
            from fastapi import HTTPException
            raise HTTPException(503, {"code": "auth_unavailable"}) from None
        回應.headers[網頁CSRFHeader名稱] = 結果.CSRF權杖 or ""
        _設定cookie(回應, 網頁CSRFCookie名稱, 結果.CSRF權杖 or "", 結果, 設定)
        setattr(
            請求.state, _CSRF接續狀態名稱,
            tuple(
                (鍵.decode("latin-1"), 值.decode("latin-1"))
                for 鍵, 值 in 回應.headers.raw
                if 鍵.lower() in {網頁CSRFHeader名稱.lower().encode("ascii"), b"set-cookie"}
            ),
        )
        return 結果.使用者
    _CSRF相依項清單.add(驗證並輪替)
    return 驗證並輪替


def 是模組CSRF相依項(呼叫: object) -> bool:
    """只承認本模組實際建立且仍由路由持有的 dependency identity。"""
    return 呼叫 in _CSRF相依項清單


def 傳遞請求CSRF接續(請求: Request, 回應: Response) -> Response:
    """把dependency已產生但被framework validation截斷的successor轉貼到最終回應。"""
    headers = getattr(請求.state, _CSRF接續狀態名稱, ())
    if type(headers) is not tuple:
        return 回應
    for 項目 in headers:
        if type(項目) is not tuple or len(項目) != 2:
            continue
        名稱, 值 = 項目
        if type(名稱) is not str or type(值) is not str:
            continue
        小寫名稱 = 名稱.lower()
        if 小寫名稱 == 網頁CSRFHeader名稱.lower():
            回應.headers[網頁CSRFHeader名稱] = 值
        elif 小寫名稱 == "set-cookie":
            回應.headers.append("Set-Cookie", 值)
    return 回應


def _登出CSRF標記() -> None:
    """標記 logout 由 handler 使用同一 transactional revoke primitive。"""


_CSRF相依項清單.add(_登出CSRF標記)


def 建立網頁認證路由器(
    服務: 網頁工作階段服務,
    驗證器: 帳密驗證器,
    *,
    設定: 網頁安全設定,
    目前工作階段相依項=None,
) -> APIRouter:
    """建立明確injected、無global app的exact ``/api/auth`` router。

    參數:
        服務: authoritative Web工作階段服務。
        驗證器: 將帳密轉為Web使用者的驗證函式。
        設定: 與服務TTL一致的Web安全設定。
        目前工作階段相依項: 可選的canonical current-session dependency；省略時建立新實例。
    返回:
        含login、me/session與logout操作的FastAPI APIRouter。
    例外:
        ValueError: 服務、驗證器、設定或注入相依不符合Web認證契約。
    副作用:
        建立路由並以弱參照登錄其authoritative TTL；不建立global app或資料庫連線。
    """
    if (
        not _是工作階段服務(服務)
        or type(設定) is not 網頁安全設定
        or _讀取核准工作階段TTL(服務) != 設定.工作階段有效秒數
        or not callable(驗證器)
        or (目前工作階段相依項 is not None and not callable(目前工作階段相依項))
    ):
        raise ValueError("Web認證設定無效")
    路由器 = APIRouter(prefix="/api/auth", tags=["web-auth"])
    目前工作階段相依 = 目前工作階段相依項 or 建立目前工作階段相依項(服務, 設定)
    @路由器.post(
        "/login", operation_id="登入網頁認證工作階段_api_auth_login_post",
        response_model=認證工作階段回應模型,
        responses={200: {"headers": _SET_COOKIE文件}, 401: {}, 422: {}, 503: {}},
        openapi_extra={"parameters": [_COOKIE參數(網頁工作階段Cookie名稱, False)]},
    )
    def 登入(請求資料: 登入請求模型, 請求: Request, 回應: Response):
        """驗證 exact JSON 帳密後防 fixation 地發行新 pair。"""
        if 請求.headers.get("content-type") != "application/json":
            return _錯誤(422, "request_invalid")
        if len(請求資料.使用者名稱.encode("utf-8")) > 512 or len(請求資料.密碼.encode("utf-8")) > 1024:
            return _錯誤(422, "request_invalid")
        try:
            使用者 = 驗證器(請求資料.使用者名稱, 請求資料.密碼)
            舊cookie狀態, 舊權杖 = _讀取raw_cookie(請求, 網頁工作階段Cookie名稱)
            if 舊cookie狀態 == 2:
                return _錯誤(401, "invalid_credentials")
            結果 = 服務.發行(使用者, 舊權杖, 請求.headers.get("user-agent"))
        except ValueError:
            return _錯誤(401, "invalid_credentials")
        except 網頁認證不可用:
            return _錯誤(503, "auth_unavailable")
        _設定cookie(回應, 網頁工作階段Cookie名稱, 結果.工作階段權杖 or "", 結果, 設定)
        _設定cookie(回應, 網頁CSRFCookie名稱, 結果.CSRF權杖 or "", 結果, 設定)
        return _建立DTO(結果)
    @路由器.get(
        "/me", operation_id="取得目前網頁認證使用者_api_auth_me_get",
        response_model=認證工作階段回應模型,
        responses={200: {"headers": _SUCCESSOR文件}, 401: {}, 503: {}},
        openapi_extra={"parameters": [
            _COOKIE參數(網頁工作階段Cookie名稱, True),
            _COOKIE參數(網頁CSRFCookie名稱, False),
        ]},
    )
    @路由器.get(
        "/session", operation_id="取得網頁認證工作階段_api_auth_session_get",
        response_model=認證工作階段回應模型,
        responses={200: {"headers": _SUCCESSOR文件}, 401: {}, 503: {}},
        openapi_extra={"parameters": [
            _COOKIE參數(網頁工作階段Cookie名稱, True),
            _COOKIE參數(網頁CSRFCookie名稱, False),
        ]},
    )
    def 取得工作階段(
        請求: Request, 回應: Response,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ):
        """透過 canonical current-session hook 驗證並 recovery-rotate。"""
        CSRF權杖 = 回應.headers.get(網頁CSRFHeader名稱) or _讀取cookie(請求, 網頁CSRFCookie名稱)
        if CSRF權杖 is None:
            raise HTTPException(503, {"code": "auth_unavailable"})
        return _建立目前工作階段DTO(使用者, CSRF權杖)
    @路由器.post(
        "/logout", operation_id="登出網頁認證工作階段_api_auth_logout_post",
        status_code=204, response_class=Response, responses={401: {}, 403: {}, 503: {}},
        dependencies=[Depends(_登出CSRF標記)],
        openapi_extra={"parameters": [
            _COOKIE參數(網頁工作階段Cookie名稱, True), _HEADER參數,
        ], "responses": {"204": {"headers": _SET_COOKIE文件}}},
    )
    def 登出(請求: Request) -> Response:
        """session precedence 後驗證 CSRF，撤銷並總是清 cookie。"""
        工作階段權杖 = _讀取cookie(請求, 網頁工作階段Cookie名稱)
        CSRF權杖 = _讀取header(請求, 網頁CSRFHeader名稱)
        if 工作階段權杖 is None:
            return _錯誤(401, "unauthorized", 設定)
        try:
            服務.撤銷(工作階段權杖, CSRF權杖)
        except 網頁未授權:
            return _錯誤(401, "unauthorized", 設定)
        except 網頁CSRF無效:
            return _錯誤(403, "csrf_invalid", 設定)
        except 網頁認證不可用:
            return _錯誤(503, "auth_unavailable", 設定)
        回應 = Response(status_code=204)
        _清除cookie(回應, 設定)
        return 回應
    _登錄網頁認證路由器TTL(路由器, _讀取核准工作階段TTL(服務))
    return 路由器
