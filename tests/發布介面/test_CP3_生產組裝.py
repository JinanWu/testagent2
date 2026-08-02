"""CP3-COMP：不可變生產設定與base composition測試。"""

from dataclasses import FrozenInstanceError

import pytest

from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.生產組裝 import (
    生產相依項建構器,
    建立生產應用程式,
    建立生產相依項,
)
from 繁中代理.發布介面.路由.網頁認證 import 建立網頁認證路由器
from fastapi.testclient import TestClient


def test_生產設定缺必要DB_origin_provider皆fail_closed(tmp_path):
    """DB、exact origin與provider任一缺少或不合法皆立即拒絕。"""
    資料庫 = tmp_path / "production.sqlite3"
    有效 = {"資料庫路徑": 資料庫, "允許來源": ("https://web.example",), "模型供應器": "fake", "模型名稱": "fake"}
    for 覆寫 in (
        {"資料庫路徑": None},
        {"允許來源": ()},
        {"允許來源": ("*",)},
        {"模型供應器": ""},
    ):
        with pytest.raises(ValueError, match="^生產設定無效$"):
            生產設定(**(有效 | 覆寫))


def test_生產設定不可變且允許loopback開發cookie(tmp_path):
    """loopback HTTP可明確關閉secure cookie，且設定值不可在建立後改寫。"""
    設定 = 生產設定(
        tmp_path / "development.sqlite3",
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )
    assert 設定.建立網頁安全設定().允許來源 == ("http://localhost:5173",)
    with pytest.raises(FrozenInstanceError):
        設定.模型供應器 = "other"


class _資源:
    """記錄生產lifespan是否精確關閉。"""

    def __init__(self, 紀錄):
        """保存測試紀錄。"""
        self.紀錄 = 紀錄

    async def 關閉(self):
        """記錄一次關閉。"""
        self.紀錄.append("close")


class _附加相依建構器:
    """透過正式protocol提供明確資源工廠。"""

    def __init__(self, 紀錄):
        """保存測試紀錄與接收的canonical dependencies。"""
        self.紀錄 = 紀錄
        self.目前工作階段相依 = None

    def 建立附加相依項(self, 設定, 目前工作階段相依, CSRF相依):
        """回傳不可變且沒有額外路由的相依項。"""
        assert callable(CSRF相依) and 設定.模型供應器 == "fake"
        self.目前工作階段相依 = 目前工作階段相依

        async def 建立資源():
            """每次startup建立新的明確資源。"""
            self.紀錄.append("start")
            return _資源(self.紀錄)

        return 發布介面相依項((), (建立資源,))


def test_生產base組裝auth_health_openapi與生命週期exact_once(tmp_path):
    """base composition不靠global runtime，並由lifespan擁有明確資源。"""
    紀錄 = []
    建構器 = _附加相依建構器(紀錄)
    設定 = 生產設定(tmp_path / "app.sqlite3", ("https://web.example",), "fake", "fake")
    應用 = 建立生產應用程式(設定, 建構器)
    me路由 = next(路由 for 路由 in 應用.routes if getattr(路由, "path", None) == "/api/auth/me")
    assert me路由.dependant.dependencies[0].call is 建構器.目前工作階段相依
    with TestClient(應用) as 客戶端:
        assert 客戶端.get("/healthz").json() == {"status": "ok"}
        assert "/api/auth/me" in 客戶端.get("/openapi.json").json()["paths"]
        assert 紀錄 == ["start"]
    assert 紀錄 == ["start", "close"]


def test_生產組裝拒絕非protocol結果(tmp_path):
    """hostile builder不可注入mutable或非canonical composition。"""
    class 錯誤建構器:
        """回傳不合法相依值。"""
        def 建立附加相依項(self, *_):
            """模擬錯誤建構結果。"""
            return []

    設定 = 生產設定(tmp_path / "bad.sqlite3", ("https://web.example",), "fake", "fake")
    with pytest.raises(ValueError, match="^生產組裝無效$"):
        建立生產應用程式(設定, 錯誤建構器())


def test_生產組裝保留建構器程式錯誤identity與traceback(tmp_path):
    """建構器內部錯誤不是契約回傳驗證，不得被正規化而失去診斷。"""
    預期錯誤 = RuntimeError("builder defect")

    class 故障建構器:
        """拋出可識別的非契約錯誤。"""

        def 建立附加相依項(self, *_):
            """模擬建構器實作錯誤。"""
            raise 預期錯誤

    設定 = 生產設定(tmp_path / "failure.sqlite3", ("https://web.example",), "fake", "fake")
    with pytest.raises(RuntimeError) as 捕捉:
        建立生產相依項(設定, 故障建構器())

    assert 捕捉.value is 預期錯誤
    assert 捕捉.traceback[-1].name == "建立附加相依項"


def test_新增公開組裝與設定API文件完整():
    """公開CP3 API皆明示參數、返回、例外與副作用。"""
    公開API = (
        生產設定,
        生產設定.建立網頁安全設定,
        生產相依項建構器,
        生產相依項建構器.建立附加相依項,
        建立生產相依項,
        建立生產應用程式,
        建立網頁認證路由器,
    )
    for API in 公開API:
        文件 = __import__("inspect").getdoc(API) or ""
        assert all(標題 in 文件 for 標題 in ("參數:", "返回:", "例外:", "副作用:")), API
