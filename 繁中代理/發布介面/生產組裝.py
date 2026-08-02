"""CP3 production base composition；只由明確設定與建構器建立資源。"""

from __future__ import annotations

from typing import Callable, Protocol

from fastapi import FastAPI

from .相依項 import 發布介面相依項
from .應用程式 import 建立網頁應用程式
from .設定 import 生產設定
from .網頁工作階段 import 網頁使用者, 網頁工作階段服務
from .路由.網頁認證 import (
    建立CSRF相依項,
    建立SQLite帳密驗證器,
    建立目前工作階段相依項,
    建立網頁認證路由器,
)

目前工作階段相依型別 = Callable[..., 網頁使用者]
CSRF相依型別 = Callable[..., 網頁使用者]


class 生產相依項建構器(Protocol):
    """以canonical auth hooks附加routers與resources的建構契約。

    參數:
        實作者由呼叫端提供，且不得依賴module-global runtime。
    返回:
        實作者須提供符合本protocol的建構器物件。
    例外:
        實作者的程式錯誤會由生產組裝原樣傳出。
    副作用:
        protocol本身無副作用；實作者副作用須由其文件明示。
    """

    def 建立附加相依項(
        self,
        設定: 生產設定,
        目前工作階段相依: 目前工作階段相依型別,
        CSRF相依: CSRF相依型別,
    ) -> 發布介面相依項:
        """建立不可變的附加composition。

        參數:
            設定: 已驗證的不可變生產設定。
            目前工作階段相依: canonical current-session dependency。
            CSRF相依: canonical single-use CSRF dependency。
        返回:
            僅含附加routers與lifespan資源工廠的發布介面相依項。
        例外:
            實作者定義的例外；呼叫端會保留其identity與traceback。
        副作用:
            不得建立module-global runtime；其他副作用由實作者負責揭露。
        """
        ...


def 建立生產相依項(
    設定: 生產設定,
    建構器: 生產相依項建構器 | None = None,
) -> 發布介面相依項:
    """建立auth base與明確附加相依。

    參數:
        設定: 已驗證的不可變生產設定。
        建構器: 可選的附加composition建構器。
    返回:
        含canonical auth router及附加routers/resources的不可變相依項。
    例外:
        ValueError: 設定型別或建構器回傳值違反composition契約。
        Exception: 建構器內部錯誤保持原identity與traceback傳出。
    副作用:
        呼叫建構器並登錄認證router的弱參照metadata；不讀環境、不連線資料庫。
    """
    if type(設定) is not 生產設定:
        raise ValueError("生產組裝無效")
    網頁設定 = 設定.建立網頁安全設定()
    工作階段服務 = 網頁工作階段服務(
        設定.資料庫路徑,
        有效秒數=設定.工作階段有效秒數,
    )
    目前工作階段相依 = 建立目前工作階段相依項(工作階段服務, 網頁設定)
    CSRF相依 = 建立CSRF相依項(工作階段服務, 網頁設定)
    認證路由器 = 建立網頁認證路由器(
        工作階段服務,
        建立SQLite帳密驗證器(設定.資料庫路徑),
        設定=網頁設定,
        目前工作階段相依項=目前工作階段相依,
    )
    if 建構器 is None:
        附加相依項 = 發布介面相依項((), ())
    else:
        附加相依項 = 建構器.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        if type(附加相依項) is not 發布介面相依項:
            raise ValueError("生產組裝無效")
    return 發布介面相依項(
        (認證路由器, *附加相依項.路由器清單),
        附加相依項.資源工廠清單,
    )


def 建立生產應用程式(
    設定: 生產設定,
    建構器: 生產相依項建構器 | None = None,
) -> FastAPI:
    """由不可變設定建立可啟動的生產FastAPI應用程式。

    參數:
        設定: 已驗證的不可變生產設定。
        建構器: 可選的附加composition建構器。
    返回:
        已組裝routers、middleware與lifespan資源工廠的FastAPI應用程式。
    例外:
        ValueError: 設定、路由或建構器回傳值違反組裝契約。
        Exception: 建構器內部錯誤保持原identity與traceback傳出。
    副作用:
        呼叫建構器並建立應用程式物件；資源只在lifespan startup建立。
    """
    return 建立網頁應用程式(建立生產相依項(設定, 建構器), 設定.建立網頁安全設定())
