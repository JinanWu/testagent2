"""Canonical 發布介面 FastAPI application factory。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .相依項 import 發布介面相依項, 發布介面資源
from .設定 import (
    允許路由前綴,
    啟動錯誤訊息,
    發布介面標題,
    發布介面版本,
    路由設定錯誤訊息,
    關閉錯誤訊息,
)

_控制流程錯誤 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _拋出固定錯誤(訊息: str) -> NoReturn:
    """在原始 except scope 外建立不帶例外鏈的一般錯誤。"""
    raise RuntimeError(訊息) from None


def _拋出控制流程(錯誤: BaseException) -> NoReturn:
    """保留 KISG 物件 identity 與 args。"""
    raise 錯誤


async def _反向關閉資源(資源清單: list[發布介面資源]) -> BaseException | None:
    """反向且 exact-once 關閉所有已啟動資源，回傳最高優先失敗。"""
    第一個一般錯誤: BaseException | None = None
    第一個控制流程: BaseException | None = None
    while 資源清單:
        資源 = 資源清單.pop()
        try:
            await 資源.關閉()
        except BaseException as 錯誤:
            if isinstance(錯誤, _控制流程錯誤):
                if 第一個控制流程 is None:
                    第一個控制流程 = 錯誤
            elif 第一個一般錯誤 is None:
                第一個一般錯誤 = 錯誤
        finally:
            資源 = None
    return 第一個控制流程 if 第一個控制流程 is not None else 第一個一般錯誤


def _驗證路由清單(相依項: 發布介面相依項) -> None:
    """掛載前驗證 namespace、HTTP inventory 與 method/path 唯一性。"""
    已見操作: set[tuple[str, str]] = {("GET", "/healthz")}
    允許集合 = set(允許路由前綴)
    for 路由器 in 相依項.路由器清單:
        前綴 = 路由器.prefix
        if 前綴 not in 允許集合:
            raise ValueError(路由設定錯誤訊息)
        for 路由 in 路由器.routes:
            if not isinstance(路由, APIRoute):
                raise ValueError(路由設定錯誤訊息)
            路徑 = 路由.path
            if 路徑 != 前綴 and not 路徑.startswith(前綴 + "/"):
                raise ValueError(路由設定錯誤訊息)
            for 方法 in 路由.methods:
                操作 = (方法, 路徑)
                if 操作 in 已見操作:
                    raise ValueError(路由設定錯誤訊息)
                已見操作.add(操作)


def 建立生命週期(相依項: 發布介面相依項):
    """建立只擁有 injected factory products 的 fail-closed lifespan。"""

    @asynccontextmanager
    async def 生命週期(應用程式: FastAPI) -> AsyncIterator[None]:
        """依序啟動資源，失敗即反向清理；正常結束亦反向 exact-once 關閉。"""
        已啟動資源: list[發布介面資源] = []
        try:
            for 工廠 in 相依項.資源工廠清單:
                已啟動資源.append(await 工廠())
        except BaseException as 啟動錯誤:
            清理錯誤 = await _反向關閉資源(已啟動資源)
            if isinstance(啟動錯誤, _控制流程錯誤):
                _拋出控制流程(啟動錯誤)
            if isinstance(清理錯誤, _控制流程錯誤):
                _拋出控制流程(清理錯誤)
            啟動錯誤 = None
            清理錯誤 = None
            _拋出固定錯誤(啟動錯誤訊息)

        應用程式.state.發布介面相依項 = 相依項
        應用程式.state.發布介面資源 = tuple(已啟動資源)
        try:
            yield
        finally:
            del 應用程式.state.發布介面資源
            del 應用程式.state.發布介面相依項
            清理錯誤 = await _反向關閉資源(已啟動資源)
            if isinstance(清理錯誤, _控制流程錯誤):
                _拋出控制流程(清理錯誤)
            if 清理錯誤 is not None:
                清理錯誤 = None
                _拋出固定錯誤(關閉錯誤訊息)

    return 生命週期


def 建立應用程式(相依項: 發布介面相依項) -> FastAPI:
    """由 exact composition 建立互相隔離、無 process-global request state 的 app。"""
    if type(相依項) is not 發布介面相依項:
        raise ValueError(路由設定錯誤訊息)
    _驗證路由清單(相依項)
    應用程式 = FastAPI(
        title=發布介面標題,
        version=發布介面版本,
        openapi_url="/openapi.json",
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
        lifespan=建立生命週期(相依項),
    )

    @應用程式.get("/healthz", status_code=200)
    def 取得健康狀態() -> dict[str, str]:
        """回傳不接觸任何 injected resource 的固定健康狀態。"""
        return {"status": "ok"}

    for 路由器 in 相依項.路由器清單:
        應用程式.include_router(路由器)
    return 應用程式
