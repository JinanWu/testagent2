"""CP3 Web 工作階段列表與詳情的 exact-prefix 路由工廠。"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from ..Web代理服務 import (
    Web服務不可用,
    Web請求無效,
    Web資源不存在,
    工作階段列表項目,
    工作階段詳情,
    序列化工作階段列表,
    序列化工作階段詳情,
)
from ..網頁工作階段 import 網頁使用者


class 工作階段查詢服務(Protocol):
    """sessions routes 所需的最小安全查詢契約。"""

    def 列出工作階段(self, 使用者識別碼: str, 數量上限: int = 20) -> tuple[工作階段列表項目, ...]:
        """依登入 owner 列出 Web logical roots。"""
        ...

    def 讀取工作階段(self, 使用者識別碼: str, 根工作階段識別碼: str) -> 工作階段詳情:
        """依登入 owner 讀取 logical root 的安全 transcript。"""
        ...


def 建立工作階段路由器(服務: 工作階段查詢服務, 目前工作階段相依) -> APIRouter:
    """注入 caller 的 canonical current-session dependency 並建立兩個 GET routes。"""
    路由器 = APIRouter(prefix="/api/sessions")

    @路由器.get("")
    def 列出工作階段(
        請求: Request,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> dict[str, object]:
        """列出 current-session user 的 bounded Web 工作階段 allowlist。"""
        數量上限 = _解析數量上限(請求)
        try:
            return 序列化工作階段列表(服務.列出工作階段(使用者.識別碼, 數量上限))
        except Web請求無效:
            raise HTTPException(status_code=400, detail={"code": "invalid_request"}) from None
        except Web服務不可用:
            raise HTTPException(status_code=503, detail={"code": "sessions_unavailable"}) from None

    @路由器.get("/{session_id}")
    def 讀取工作階段(
        工作階段識別碼: Annotated[str, Path(alias="session_id")],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> dict[str, object]:
        """讀取 current-session user 可見 logical root 的最小詳情。"""
        根識別碼 = _驗證路徑識別碼(工作階段識別碼)
        try:
            return 序列化工作階段詳情(服務.讀取工作階段(使用者.識別碼, 根識別碼))
        except Web請求無效:
            raise HTTPException(status_code=400, detail={"code": "invalid_request"}) from None
        except Web資源不存在:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"}) from None
        except Web服務不可用:
            raise HTTPException(status_code=503, detail={"code": "sessions_unavailable"}) from None

    return 路由器


def _解析數量上限(請求: Request) -> int:
    """手動驗證唯一 limit query，失敗時只回固定 422 code。"""
    值清單 = 請求.query_params.getlist("limit")
    if not 值清單:
        return 20
    值 = 值清單[0]
    if len(值清單) != 1 or not 1 <= len(值) <= 2 or not 值.isascii() or not 值.isdecimal():
        raise HTTPException(status_code=422, detail={"code": "invalid_request"})
    數量上限 = int(值)
    if not 1 <= 數量上限 <= 50:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"})
    return 數量上限


def _驗證路徑識別碼(值: str) -> str:
    """手動驗證 session path ID，避免 validation detail 回顯原值。"""
    if not 1 <= len(值) <= 128 or 值.strip() != 值:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"})
    return 值
