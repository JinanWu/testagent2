"""CP3 Web 技能列表與詳情的 exact-prefix 路由工廠。"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path

from ..Web代理服務 import (
    Web服務不可用,
    Web請求無效,
    Web資源不存在,
    技能詳情,
    技能項目,
    序列化技能列表,
    序列化技能詳情,
)
from ..網頁工作階段 import 網頁使用者


class 技能查詢服務(Protocol):
    """skills routes 所需的最小安全查詢契約。"""

    def 列出技能(self, 使用者識別碼: str) -> tuple[技能項目, ...]:
        """列出登入使用者的唯一可見技能。"""
        ...

    def 讀取技能(self, 使用者識別碼: str, 技能識別碼: str) -> 技能詳情:
        """讀取登入使用者可見技能的安全內容。"""
        ...


def 建立技能路由器(服務: 技能查詢服務, 目前工作階段相依) -> APIRouter:
    """注入 caller 的 canonical current-session dependency 並建立兩個 GET routes。"""
    路由器 = APIRouter(prefix="/api/skills")

    @路由器.get("")
    def 列出技能(使用者: 網頁使用者 = Depends(目前工作階段相依)) -> dict[str, object]:
        """列出 current-session user 的技能 metadata allowlist。"""
        try:
            return 序列化技能列表(服務.列出技能(使用者.識別碼))
        except Web服務不可用:
            raise HTTPException(status_code=503, detail={"code": "skills_unavailable"}) from None

    @路由器.get("/{skill_id}")
    def 讀取技能(
        技能路徑識別碼: Annotated[str, Path(alias="skill_id")],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> dict[str, object]:
        """讀取 current-session user 可見技能的完整 bounded SKILL.md。"""
        技能識別碼 = _驗證路徑識別碼(技能路徑識別碼)
        try:
            return 序列化技能詳情(服務.讀取技能(使用者.識別碼, 技能識別碼))
        except Web請求無效:
            raise HTTPException(status_code=400, detail={"code": "invalid_request"}) from None
        except Web資源不存在:
            raise HTTPException(status_code=404, detail={"code": "skill_not_found"}) from None
        except Web服務不可用:
            raise HTTPException(status_code=503, detail={"code": "skills_unavailable"}) from None

    return 路由器


def _驗證路徑識別碼(值: str) -> str:
    """手動驗證 skill path ID，失敗時不回顯 rejected path。"""
    if not 1 <= len(值) <= 128 or 值.strip() != 值:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"})
    return 值
