"""Canonical 發布介面 FastAPI application factory。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import NoReturn

from fastapi import APIRouter, FastAPI
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
_允許方法 = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"})
_最大路由器數 = 64
_最大資源工廠數 = 64
_最大路由數 = 512
_路由器清單描述器 = 發布介面相依項.__dict__["路由器清單"]
_資源工廠清單描述器 = 發布介面相依項.__dict__["資源工廠清單"]


def _拋出固定錯誤(訊息: str) -> NoReturn:
    """在原始 except scope 外建立不帶例外鏈的一般錯誤。"""
    raise RuntimeError(訊息) from None


def _拋出盒中控制流程(錯誤盒: list[BaseException]) -> NoReturn:
    """保留控制流程 identity，並在 traceback frame 清除其參照。"""
    try:
        raise 錯誤盒.pop()
    finally:
        錯誤盒.clear()


async def _反向關閉資源(
    資源清單: list[發布介面資源],
) -> tuple[list[BaseException], bool]:
    """反向 exact-once 全清理；第一個控制流程依 reverse 順序勝出。"""
    控制流程盒: list[BaseException] = []
    有一般錯誤 = False
    資源 = None
    錯誤 = None
    try:
        while 資源清單:
            資源 = 資源清單.pop()
            try:
                await 資源.關閉()
            except BaseException as 捕捉錯誤:
                錯誤 = 捕捉錯誤
                if isinstance(錯誤, _控制流程錯誤):
                    if not 控制流程盒:
                        控制流程盒.append(錯誤)
                else:
                    有一般錯誤 = True
            finally:
                資源 = None
                錯誤 = None
        return 控制流程盒, 有一般錯誤
    finally:
        資源 = None
        錯誤 = None
        資源清單.clear()


def _重建相依項(相依項: 發布介面相依項) -> 發布介面相依項:
    """用 dataclass slot descriptors 驗證並重建 module-owned exact tuples。"""
    路由器原值 = None
    工廠原值 = None
    try:
        if type(相依項) is not 發布介面相依項:
            raise ValueError(路由設定錯誤訊息)
        路由器原值 = _路由器清單描述器.__get__(相依項, 發布介面相依項)
        工廠原值 = _資源工廠清單描述器.__get__(相依項, 發布介面相依項)
        if type(路由器原值) is not tuple or type(工廠原值) is not tuple:
            raise ValueError(路由設定錯誤訊息)
        if len(路由器原值) > _最大路由器數 or len(工廠原值) > _最大資源工廠數:
            raise ValueError(路由設定錯誤訊息)
        路由器副本: list[APIRouter] = []
        已見路由器: set[int] = set()
        for 路由器 in 路由器原值:
            if type(路由器) is not APIRouter or id(路由器) in 已見路由器:
                raise ValueError(路由設定錯誤訊息)
            已見路由器.add(id(路由器))
            路由器副本.append(路由器)
        工廠副本 = []
        for 工廠 in 工廠原值:
            if not callable(工廠):
                raise ValueError(路由設定錯誤訊息)
            工廠副本.append(工廠)
        return 發布介面相依項(tuple(路由器副本), tuple(工廠副本))
    finally:
        del 相依項
        路由器原值 = None
        工廠原值 = None


def _讀取路由描述(路由器清單: tuple[APIRouter, ...]):
    """驗證完整 HTTP inventory 並擷取可 replay 的 identity/value 描述。"""
    已見操作: set[tuple[str, str]] = {("GET", "/healthz")}
    描述清單 = []
    路由總數 = 0
    for 路由器 in 路由器清單:
        前綴 = 路由器.prefix
        路由清單 = 路由器.routes
        if type(前綴) is not str or 前綴 not in 允許路由前綴 or type(路由清單) is not list:
            raise ValueError(路由設定錯誤訊息)
        路由總數 += len(路由清單)
        if 路由總數 > _最大路由數:
            raise ValueError(路由設定錯誤訊息)
        路由描述 = []
        for 路由 in 路由清單:
            if type(路由) is not APIRoute:
                raise ValueError(路由設定錯誤訊息)
            路徑 = 路由.path
            方法集合 = 路由.methods
            if type(路徑) is not str or (路徑 != 前綴 and not 路徑.startswith(前綴 + "/")):
                raise ValueError(路由設定錯誤訊息)
            if type(方法集合) not in (set, frozenset) or not 方法集合 or len(方法集合) > len(_允許方法):
                raise ValueError(路由設定錯誤訊息)
            方法描述 = []
            for 方法 in 方法集合:
                if type(方法) is not str or 方法 not in _允許方法 or 方法 != 方法.upper():
                    raise ValueError(路由設定錯誤訊息)
                if (方法, 路徑) in 已見操作:
                    raise ValueError(路由設定錯誤訊息)
                已見操作.add((方法, 路徑))
                方法描述.append(方法)
            路由描述.append((id(路由), 路徑, id(方法集合), tuple(sorted(方法描述))))
        描述清單.append((id(路由器), id(路由清單), tuple(路由描述)))
    return tuple(描述清單), frozenset(已見操作)


def _重播路由描述(路由器清單: tuple[APIRouter, ...], 預期描述) -> None:
    """拒絕 validation 與 include 間的 router/route/method/path mutation。"""
    目前描述, _ = _讀取路由描述(路由器清單)
    if 目前描述 != 預期描述:
        raise ValueError(路由設定錯誤訊息)


def _驗證應用路由(應用程式: FastAPI, 預期操作: frozenset[tuple[str, str]]) -> None:
    """include 後重驗 final APIRoute inventory，禁止空 methods fail-open。"""
    實際操作: set[tuple[str, str]] = set()
    for 路由 in 應用程式.routes:
        if isinstance(路由, APIRoute):
            方法集合 = 路由.methods
            if type(路由) is not APIRoute or type(方法集合) not in (set, frozenset) or not 方法集合:
                raise ValueError(路由設定錯誤訊息)
            for 方法 in 方法集合:
                if type(方法) is not str or 方法 not in _允許方法:
                    raise ValueError(路由設定錯誤訊息)
                if (方法, 路由.path) in 實際操作:
                    raise ValueError(路由設定錯誤訊息)
                實際操作.add((方法, 路由.path))
    if 實際操作 != set(預期操作):
        raise ValueError(路由設定錯誤訊息)


def 建立生命週期(相依項: 發布介面相依項):
    """建立只擁有 reconstructed factory tuple products 的 fail-closed lifespan。"""

    @asynccontextmanager
    async def 生命週期(應用程式: FastAPI) -> AsyncIterator[None]:
        """全清理後依 body/control/ordinary 的固定優先序結束。"""
        已啟動資源: list[發布介面資源] = []
        啟動錯誤盒: list[BaseException] = []
        try:
            for 工廠 in 相依項.資源工廠清單:
                if not callable(工廠):
                    raise RuntimeError(啟動錯誤訊息)
                已啟動資源.append(await 工廠())
        except BaseException as 啟動錯誤:
            啟動錯誤盒.append(啟動錯誤)
        工廠 = None
        if 啟動錯誤盒:
            清理控制盒, _ = await _反向關閉資源(已啟動資源)
            if isinstance(啟動錯誤盒[0], _控制流程錯誤):
                清理控制盒.clear()
                _拋出盒中控制流程(啟動錯誤盒)
            啟動錯誤盒.clear()
            if 清理控制盒:
                _拋出盒中控制流程(清理控制盒)
            _拋出固定錯誤(啟動錯誤訊息)

        應用程式.state.發布介面相依項 = 相依項
        應用程式.state.發布介面資源 = tuple(已啟動資源)
        主體錯誤盒: list[BaseException] = []
        try:
            try:
                yield
            except BaseException as 主體錯誤:
                主體錯誤盒.append(主體錯誤)
        finally:
            del 應用程式.state.發布介面資源
            del 應用程式.state.發布介面相依項
            清理控制盒, 有清理一般錯誤 = await _反向關閉資源(已啟動資源)
        if 主體錯誤盒 and isinstance(主體錯誤盒[0], _控制流程錯誤):
            清理控制盒.clear()
            _拋出盒中控制流程(主體錯誤盒)
        if 清理控制盒:
            主體錯誤盒.clear()
            _拋出盒中控制流程(清理控制盒)
        有主體一般錯誤 = bool(主體錯誤盒)
        主體錯誤盒.clear()
        if 有主體一般錯誤 or 有清理一般錯誤:
            _拋出固定錯誤(關閉錯誤訊息)

    return 生命週期


def 建立應用程式(相依項: 發布介面相依項) -> FastAPI:
    """由 exact reconstructed composition 建立隔離 app。"""
    安全相依項 = _重建相依項(相依項)
    路由描述, 預期操作 = _讀取路由描述(安全相依項.路由器清單)
    應用程式 = FastAPI(
        title=發布介面標題,
        version=發布介面版本,
        openapi_url="/openapi.json",
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
        lifespan=建立生命週期(安全相依項),
    )

    @應用程式.get("/healthz", status_code=200)
    def 取得健康狀態() -> dict[str, str]:
        """回傳不接觸任何 injected resource 的固定健康狀態。"""
        return {"status": "ok"}

    for 路由器 in 安全相依項.路由器清單:
        應用程式.include_router(路由器)
    _重播路由描述(安全相依項.路由器清單, 路由描述)
    _驗證應用路由(應用程式, 預期操作)
    del 相依項
    return 應用程式
