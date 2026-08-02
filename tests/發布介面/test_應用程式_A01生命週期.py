"""AUTH A01 lifespan body/cleanup precedence 與 traceback hygiene。"""

import asyncio

import pytest

from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 關閉錯誤訊息


class _自訂Base錯誤(BaseException):
    """非 KISG 的 hostile BaseException。"""


class _資源:
    """可記錄順序與拋錯的 close resource。"""

    def __init__(self, 名稱, 事件, 錯誤=None):
        self.名稱 = 名稱
        self.事件 = 事件
        self.錯誤 = 錯誤
        self.次數 = 0

    async def 關閉(self):
        self.次數 += 1
        self.事件.append(f"close:{self.名稱}")
        if self.錯誤 is not None:
            raise self.錯誤


def _工廠(資源, 事件):
    """建立記錄 startup 的 async factory。"""

    async def 建立():
        事件.append(f"start:{資源.名稱}")
        return 資源

    return 建立


def _捕捉(主體錯誤, *資源):
    """在 asyncio runner 改寫控制流程前保留 production traceback。"""
    事件 = 資源[0].事件 if 資源 else []
    相依項 = 發布介面相依項((), tuple(_工廠(項目, 事件) for 項目 in 資源))
    應用程式 = 建立應用程式(相依項)

    async def 進入():
        錯誤盒 = []
        追蹤盒 = []
        try:
            async with 應用程式.router.lifespan_context(應用程式):
                if 主體錯誤 is not None:
                    raise 主體錯誤
        except BaseException as 錯誤:
            錯誤盒.append(錯誤)
            追蹤盒.append(錯誤.__traceback__)
        if not 錯誤盒:
            raise AssertionError("預期錯誤")
        return 錯誤盒.pop(), 追蹤盒.pop()

    return asyncio.run(進入())


def _含標記(值, 標記, 已見):
    """有限遞迴檢查 traceback locals 可達物件。"""
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str and 值 == 標記:
        return True
    if isinstance(值, BaseException):
        return any(_含標記(項目, 標記, 已見) for 項目 in 值.args)
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(項目, 標記, 已見) for 項目 in 值)
    if type(值) is dict:
        return any(_含標記(項目, 標記, 已見) for 配對 in 值.items() for 項目 in 配對)
    if hasattr(值, "__dict__") and type(值).__module__.startswith(("繁中代理", "test_")):
        return _含標記(vars(值), 標記, 已見)
    return False


def _斷言production_frames無標記(追蹤, *標記清單):
    """掃描每個應用程式 production traceback frame local。"""
    已見production = False
    while 追蹤 is not None:
        if 追蹤.tb_frame.f_code.co_filename.endswith("發布介面/應用程式.py"):
            已見production = True
            for 值 in tuple(追蹤.tb_frame.f_locals.values()):
                for 標記 in 標記清單:
                    assert not _含標記(值, 標記, set()), 追蹤.tb_frame.f_code.co_name
        追蹤 = 追蹤.tb_next
    assert 已見production


@pytest.mark.parametrize("主體類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("關閉類型", [RuntimeError, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_body_KISG勝過所有shutdown錯誤且完整清理(主體類型, 關閉類型):
    """body control identity/args 永遠是 primary，cleanup loser 不得洩漏。"""
    事件 = []
    主體錯誤 = 主體類型("body-private")
    關閉錯誤 = 關閉類型("cleanup-private")
    第一個 = _資源("one", 事件)
    第二個 = _資源("two", 事件, 關閉錯誤)
    捕捉錯誤, 追蹤 = _捕捉(主體錯誤, 第一個, 第二個)
    assert type(捕捉錯誤) is 主體類型
    assert 捕捉錯誤 is 主體錯誤 and 捕捉錯誤.args == ("body-private",)
    assert 事件 == ["start:one", "start:two", "close:two", "close:one"]
    assert 第一個.次數 == 第二個.次數 == 1
    _斷言production_frames無標記(追蹤, "body-private", "cleanup-private")


@pytest.mark.parametrize("主體錯誤", [RuntimeError("ordinary-body"), _自訂Base錯誤("custom-body")])
@pytest.mark.parametrize("關閉類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_body一般或custom遇shutdown_KISG由shutdown勝出(主體錯誤, 關閉類型):
    """非控制 body 不得蓋過 cleanup control。"""
    事件 = []
    關閉錯誤 = 關閉類型("cleanup-control")
    資源 = _資源("one", 事件, 關閉錯誤)
    捕捉錯誤, 追蹤 = _捕捉(主體錯誤, 資源)
    assert type(捕捉錯誤) is 關閉類型 and 捕捉錯誤 is 關閉錯誤
    assert 捕捉錯誤.__cause__ is None and 捕捉錯誤.__context__ is None
    assert 資源.次數 == 1
    _斷言production_frames無標記(追蹤, *主體錯誤.args, "cleanup-control")


def test_multiple_shutdown_controls選reverse第一個且所有資源exact_once():
    """優先序固定為反向清理時最先遇到的 control。"""
    事件 = []
    後遇到 = KeyboardInterrupt("later-control")
    先遇到 = SystemExit("first-control")
    資源 = (
        _資源("one", 事件, RuntimeError("ordinary")),
        _資源("two", 事件, 後遇到),
        _資源("three", 事件, 先遇到),
    )
    捕捉錯誤, 追蹤 = _捕捉(None, *資源)
    assert type(捕捉錯誤) is SystemExit and 捕捉錯誤 is 先遇到
    assert 事件[-3:] == ["close:three", "close:two", "close:one"]
    assert [項目.次數 for 項目 in 資源] == [1, 1, 1]
    _斷言production_frames無標記(追蹤, "first-control", "later-control", "ordinary")


@pytest.mark.parametrize("主體錯誤", [RuntimeError("body-secret"), _自訂Base錯誤("body-custom-secret")])
def test_body與cleanup一般錯誤固定generic且無chain隱私(主體錯誤):
    """所有 ordinary/custom failures 清理後映射 fresh fixed unchained error。"""
    事件 = []
    資源 = _資源("one", 事件, RuntimeError("cleanup-secret"))
    捕捉錯誤, 追蹤 = _捕捉(主體錯誤, 資源)
    assert type(捕捉錯誤) is RuntimeError and 捕捉錯誤.args == (關閉錯誤訊息,)
    assert 捕捉錯誤.__cause__ is None and 捕捉錯誤.__context__ is None
    assert 資源.次數 == 1
    _斷言production_frames無標記(追蹤, *主體錯誤.args, "cleanup-secret")
