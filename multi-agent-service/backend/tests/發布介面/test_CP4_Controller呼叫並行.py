"""CP4 Controller 同步呼叫 offload 與 lifespan restart regression tests。"""

import asyncio
import threading
import time
from typing import Any, cast

import httpx
import pytest

from 繁中代理.發布介面.契約 import 建立成功信封
from 繁中代理.發布介面.呼叫.編排器 import 呼叫成功結果
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.生產Published執行 import 生產Published執行資源, 延遲外部呼叫編排器
from 繁中代理.發布介面.路由.外部呼叫 import 建立外部呼叫路由
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef


def _成功結果(請求識別: str) -> 呼叫成功結果:
    """建立 genuine I04 success result。"""
    return 呼叫成功結果(建立成功信封(
        EndpointRef("ep-1", "demo", 1),
        InvocationRef(f"inv-{請求識別}", 請求識別),
        {"answer": "ok"},
    ))


class _慢速編排器:
    """用真實同步 sleep 觀測 event-loop 是否被 invoke route 阻塞。"""

    def __init__(self) -> None:
        self.呼叫數 = 0

    def 執行(self, 短名, 請求識別, API金鑰, 輸入, 中繼資料, 時間戳):
        """同步阻塞 0.25 秒後回傳 genuine success result。"""
        self.呼叫數 += 1
        time.sleep(0.25)
        return _成功結果(請求識別)


async def _送出兩次(應用程式):
    """在同一 ASGI app 上同時送出兩個 genuine HTTP requests。"""
    transport = httpx.ASGITransport(app=應用程式)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        開始 = time.perf_counter()
        回應 = await asyncio.gather(*(
            client.post(
                "/v1/endpoints/demo/invoke",
                headers={"Authorization": "Bearer key"},
                json={"input": {"number": 序號}},
            )
            for 序號 in (1, 2)
        ))
        return time.perf_counter() - 開始, 回應


def test_兩個同步invoke由threadpool真正並行且各自成功():
    """CP4-CONC-01：兩個 0.25 秒同步委派總時顯著低於 0.5 秒。"""
    編排器 = _慢速編排器()
    應用程式 = 建立應用程式(發布介面相依項((建立外部呼叫路由(cast(Any, 編排器)),), ()))

    經過秒數, 回應 = asyncio.run(_送出兩次(應用程式))

    assert 經過秒數 < 0.45
    assert 編排器.呼叫數 == 2
    assert [項目.status_code for 項目 in 回應] == [200, 200]
    assert [項目.json()["data"] for 項目 in 回應] == [{"answer": "ok"}, {"answer": "ok"}]


class _排水編排器:
    """以 threading events 固定 active lease 的同步委派。"""

    def __init__(self) -> None:
        self.已進入 = threading.Event()
        self.可離開 = threading.Event()

    def 執行(self, 短名, 請求識別, API金鑰, 輸入, 中繼資料, 時間戳):
        """宣告已進入後等待測試允許 lease 離開。"""
        self.已進入.set()
        self.可離開.wait(timeout=1.0)
        return _成功結果(請求識別)


async def _驗證shutdown排水不阻塞事件迴圈():
    """在 request active 時啟動 shutdown，並驗證 loop 仍可排程。"""
    代理 = 延遲外部呼叫編排器()
    編排器 = _排水編排器()
    代理._編排器 = cast(Any, 編排器)
    資源 = 生產Published執行資源(代理, cast(Any, 編排器), cast(Any, None), {})

    async def 建立資源():
        """回傳測試擁有的單一 drain resource。"""
        return 資源

    應用程式 = 建立應用程式(發布介面相依項((建立外部呼叫路由(代理),), (建立資源,)))
    生命週期 = 應用程式.router.lifespan_context(應用程式)
    await 生命週期.__aenter__()
    transport = httpx.ASGITransport(app=應用程式)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        呼叫工作 = asyncio.create_task(client.post(
            "/v1/endpoints/demo/invoke",
            headers={"Authorization": "Bearer key"}, json={"input": {}},
        ))
        for _ in range(100):
            if 編排器.已進入.is_set():
                break
            await asyncio.sleep(0.005)
        assert 編排器.已進入.is_set()
        關閉工作 = asyncio.create_task(生命週期.__aexit__(None, None, None))
        await asyncio.sleep(0.05)
        assert not 關閉工作.done()
        編排器.可離開.set()
        回應, _ = await asyncio.gather(呼叫工作, 關閉工作)
    assert 回應.status_code == 200


def test_shutdown等待active_lease期間事件迴圈仍可運作():
    """CP4-CONC-02：shutdown drain 在 worker 等待，不阻塞 ASGI event loop。"""
    asyncio.run(_驗證shutdown排水不阻塞事件迴圈())


def test_startup失敗後第二次startup仍固定錯誤且不重跑one_shot_factory():
    """CP4-LIFE-02：耗盡相依項盒後不得洩漏 raw IndexError。"""
    呼叫數 = 0

    async def 失敗工廠():
        """記錄 one-shot 呼叫並以 private ordinary error 失敗。"""
        nonlocal 呼叫數
        呼叫數 += 1
        raise ValueError("private-startup")

    應用程式 = 建立應用程式(發布介面相依項((), (失敗工廠,)))

    async def 啟動兩次():
        """依序進入同一 app 的兩個獨立 lifespan contexts。"""
        for _ in range(2):
            with pytest.raises(RuntimeError, match="^發布介面啟動失敗$") as 捕捉:
                async with 應用程式.router.lifespan_context(應用程式):
                    pass
            assert 捕捉.value.__cause__ is None
            assert 捕捉.value.__context__ is None

    asyncio.run(啟動兩次())
    assert 呼叫數 == 1
