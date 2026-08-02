"""CP3 Web 聊天的 exact-prefix FastAPI 路由工廠。"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool

from ..Web代理服務 import (
    Web服務不可用,
    Web請求無效,
    Web資源不存在,
    聊天回應,
    序列化聊天回應,
)
from ..網頁工作階段 import 網頁使用者
from ..嚴格JSON import 解析嚴格JSON


class 聊天服務(Protocol):
    """聊天 route 所需的最小 transport-neutral 服務契約。"""

    def 聊天(self, 使用者識別碼: str, 訊息: str, 工作階段識別碼: str | None = None) -> 聊天回應:
        """執行一則登入使用者訊息並回傳安全 DTO。"""
        ...


class 聊天請求(BaseModel):
    """拒絕額外欄位並在進入服務前正規化聊天本文。"""

    model_config = ConfigDict(extra="forbid")
    訊息: Annotated[StrictStr, Field(alias="message")]
    工作階段識別碼: Annotated[StrictStr | None, Field(alias="session_id")] = None

    @field_validator("訊息")
    @classmethod
    def 驗證訊息(cls, 值: str) -> str:
        """trim 訊息並限制非空與 16 KiB UTF-8 大小。"""
        整理值 = 值.strip()
        if not 整理值 or len(整理值.encode("utf-8")) > 16_384:
            raise ValueError("訊息不符合契約")
        return 整理值

    @field_validator("工作階段識別碼")
    @classmethod
    def 驗證工作階段識別碼(cls, 值: str | None) -> str | None:
        """限制 optional logical-root ID 為未帶邊界空白的 1–128 字元。"""
        if 值 is not None and (not 1 <= len(值) <= 128 or 值.strip() != 值):
            raise ValueError("工作階段識別碼不符合契約")
        return 值


def 建立聊天路由器(服務: 聊天服務, 目前工作階段相依, csrf相依) -> APIRouter:
    """注入 caller 的 canonical session/CSRF dependencies 並建立 exact POST route。"""
    路由器 = APIRouter(prefix="/api/chat")

    @路由器.post("")
    async def 聊天(
        請求: Request,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
    ) -> dict[str, object]:
        """以 current-session identity 呼叫服務，並映射固定安全錯誤碼。"""
        本文 = await _解析聊天本文(請求)
        try:
            回應 = await run_in_threadpool(
                服務.聊天, 使用者.識別碼, 本文.訊息, 本文.工作階段識別碼,
            )
            return 序列化聊天回應(回應)
        except Web請求無效:
            raise HTTPException(status_code=400, detail={"code": "invalid_request"}) from None
        except Web資源不存在:
            raise HTTPException(status_code=404, detail={"code": "session_not_found"}) from None
        except Web服務不可用:
            raise HTTPException(status_code=503, detail={"code": "chat_unavailable"}) from None

    return 路由器


async def _解析聊天本文(請求: Request) -> 聊天請求:
    """手動解析 strict body，避免 FastAPI validation error 回顯 rejected input。"""
    try:
        本文位元組 = await 請求.body()
        原始本文 = 解析嚴格JSON(本文位元組.decode("utf-8"))
        if type(原始本文) is not dict:
            raise ValueError
        return 聊天請求.model_validate(原始本文)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"}) from None
