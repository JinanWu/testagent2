"""PUB P03 值確認的更新、競態與惡意邊界矩陣。"""

import concurrent.futures
from dataclasses import replace
import threading

import pytest

from 繁中代理.發布介面.規劃 import 綱要 as 綱要模組
from 繁中代理.發布介面 import 授權工具, 授權技能
from 繁中代理.發布介面.規劃.服務 import 發布規劃服務
from 繁中代理.發布介面.規劃.權限協調 import 權限協調器, 能力摘要
from 繁中代理.發布介面.規劃.綱要 import 發布值確認, 規劃服務, 草稿存取錯誤


class _結構基底錯誤(BaseException):
    pass


class _惡意字典(dict):
    def items(self):
        raise AssertionError("不得呼叫 subclass")


def _建立():
    服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-race")
    服務.建立草稿("owner", "原始需求", {"step": 1}, 現在=100)
    return 服務


def _確認(服務, response_schema=None, 現在=120):
    return 服務.確認發布值(
        "owner", "draft-race", slug="safe-slug",
        response_schema=response_schema or {"type": "string"}, docs="文件",
        endpoint_limit=60, credential_limit=30, 現在=現在,
    )


def _含標記(值, 標記, 已看過):
    if id(值) in 已看過:
        return False
    已看過.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) in (tuple, list, set, frozenset):
        for 項目 in 值:
            if _含標記(項目, 標記, 已看過):
                return True
    elif type(值) is dict:
        for 鍵, 項目 in dict.items(值):
            if _含標記(鍵, 標記, 已看過) or _含標記(項目, 標記, 已看過):
                return True
    elif type(值).__module__.startswith("繁中代理.發布介面.規劃"):
        資料 = getattr(值, "__dict__", None)
        if type(資料) is dict and _含標記(資料, 標記, 已看過):
            return True
        for 欄位 in getattr(type(值), "__slots__", ()):
            if hasattr(值, 欄位) and _含標記(object.__getattribute__(值, 欄位), 標記, 已看過):
                return True
    return False


def _斷言P03生產frame已清除(錯誤, 標記):
    名稱 = []
    for frame, _ in __import__("traceback").walk_tb(錯誤.__traceback__):
        if frame.f_code.co_filename.endswith(("綱要.py", "服務.py")):
            名稱.append(frame.f_code.co_name)
            for 值 in tuple(frame.f_locals.values()):
                assert not _含標記(值, 標記, set()), (frame.f_code.co_name, frame.f_locals)
    assert 名稱
    return 名稱


def _建立標記權威草稿():
    標記 = "p03marker"
    擁有者, 草稿識別碼 = f"owner-{標記}", f"draft-{標記}"
    服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: 草稿識別碼)
    服務.建立草稿(擁有者, f"request-{標記}", {"outline": 標記}, 現在=100)
    摘要 = 能力摘要(
        f"revision-{標記}",
        (授權技能(f"skill-{標記}", f"summary-{標記}", "a" * 64),),
        (授權工具(f"tool-{標記}", f"tool-revision-{標記}"),),
    )
    舊確認 = 發布值確認(
        草稿識別碼, 0, f"old-{標記}",
        '{"description":"p03marker","type":"string"}', f"old-docs-{標記}", 1, 1,
    )
    服務._草稿[草稿識別碼] = replace(服務._草稿[草稿識別碼], 能力摘要=摘要, 發布確認=舊確認)
    return 服務, 擁有者, 草稿識別碼, 標記


def _標記確認(wrapper, 擁有者, 草稿識別碼, 標記, 現在):
    return wrapper.確認發布值(
        擁有者, 草稿識別碼, slug=f"new-{標記}",
        response_schema={
            "type": "object", "description": 標記,
            "properties": {f"field-{標記}": {"type": "string", "description": 標記}},
        },
        docs=f"new-docs-{標記}", endpoint_limit=60, credential_limit=30, 現在=現在,
    )


def test_綱要更新遞增世代並移除舊確認且原始需求不變():
    服務 = _建立()
    原草稿 = 服務.讀取草稿("owner", "draft-race", 現在=110)
    _確認(服務)

    新草稿 = 服務.更新草稿("owner", "draft-race", {"step": 2}, 現在=130)

    assert 新草稿._世代 == 1 and 新草稿.發布確認 is None
    assert 新草稿.原始需求 == 原草稿.原始需求 == "原始需求"
    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.讀取已確認草稿("owner", "draft-race", 現在=131)


def test_確認準備期間更新綱要會由identity_generation_CAS拒絕(monkeypatch):
    服務 = _建立()
    原驗證 = 綱要模組.Draft202012Validator.check_schema
    已進入, 可繼續 = threading.Event(), threading.Event()

    def 阻塞驗證(結構):
        已進入.set()
        assert 可繼續.wait(2)
        return 原驗證(結構)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 阻塞驗證)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(_確認, 服務)
        assert 已進入.wait(2)
        服務.更新草稿("owner", "draft-race", {"step": 2}, 現在=121)
        可繼續.set()
        with pytest.raises(草稿存取錯誤):
            future.result(timeout=2)

    assert 服務.讀取草稿("owner", "draft-race", 現在=122).發布確認 is None


def test_確認與到期刪除交錯不會復活(monkeypatch):
    服務 = _建立()
    原驗證 = 綱要模組.Draft202012Validator.check_schema
    已進入, 可繼續 = threading.Event(), threading.Event()

    def 阻塞驗證(結構):
        已進入.set()
        assert 可繼續.wait(2)
        return 原驗證(結構)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 阻塞驗證)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(_確認, 服務)
        assert 已進入.wait(2)
        with pytest.raises(草稿存取錯誤):
            服務.讀取草稿("owner", "draft-race", 現在=160)
        可繼續.set()
        with pytest.raises(草稿存取錯誤):
            future.result(timeout=2)

    assert 服務._草稿 == {}


def test_已確認讀取meta驗證不持鎖且更新後由CAS固定拒絕(monkeypatch):
    服務 = _建立()
    _確認(服務)
    原驗證 = 綱要模組.Draft202012Validator.check_schema
    已進入, 可繼續 = threading.Event(), threading.Event()
    首次 = True
    首次鎖 = threading.Lock()

    def 阻塞驗證(結構):
        nonlocal 首次
        with 首次鎖:
            此次阻塞 = 首次
            首次 = False
        if 此次阻塞:
            已進入.set()
            assert 可繼續.wait(2)
        return 原驗證(結構)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 阻塞驗證)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(服務.讀取已確認草稿, "owner", "draft-race", 現在=121)
        assert 已進入.wait(2)
        assert 服務.讀取草稿("owner", "draft-race", 現在=121).發布確認 is not None
        assert 服務.更新草稿("owner", "draft-race", {"step": 2}, 現在=121)._世代 == 1
        可繼續.set()
        with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
            future.result(timeout=2)

    assert 服務.讀取草稿("owner", "draft-race", 現在=122).發布確認 is None


def test_已確認讀取無競態時成功且回傳detached草稿():
    服務 = _建立()
    確認 = _確認(服務)

    第一份 = 服務.讀取已確認草稿("owner", "draft-race", 現在=121)
    第二份 = 服務.讀取已確認草稿("owner", "draft-race", 現在=121)

    assert 第一份 is not 第二份
    assert 第一份.發布確認 is not 確認
    assert 第一份.發布確認 == 第二份.發布確認 == 確認


def test_schema驗證只看單次走訪副本不受呼叫端同步修改(monkeypatch):
    服務 = _建立()
    原驗證 = 綱要模組.Draft202012Validator.check_schema
    已進入, 可繼續 = threading.Event(), threading.Event()
    結構 = {"type": "object", "properties": {}, "additionalProperties": False}

    def 阻塞驗證(副本):
        assert 副本 is not 結構
        已進入.set()
        assert 可繼續.wait(2)
        return 原驗證(副本)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 阻塞驗證)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(_確認, 服務, 結構)
        assert 已進入.wait(2)
        結構["type"] = "invalid"
        可繼續.set()
        確認 = future.result(timeout=2)

    assert 確認.response_schema["type"] == "object"


@pytest.mark.parametrize("結構", [_惡意字典({"type": "string"}), {"type": 7}])
def test_畸形或meta_invalid物件schema固定拒絕且不觸發subclass(結構):
    服務 = _建立()
    with pytest.raises(ValueError, match="^發布值確認輸入無效$"):
        _確認(服務, 結構)


@pytest.mark.parametrize("控制流", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_meta_validation保留控制流且清除生產frame輸入(monkeypatch, 控制流):
    marker = f"{控制流.__name__}-SCHEMA-MARKER"
    服務 = _建立()
    結構 = {"type": "object", "description": marker, "additionalProperties": False}

    def 失敗(_結構):
        raise 控制流(marker)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 失敗)
    with pytest.raises(控制流) as 錯誤:
        _確認(服務, 結構)
    for frame, _ in __import__("traceback").walk_tb(錯誤.value.__traceback__):
        if frame.f_code.co_filename.endswith(("綱要.py", "服務.py")):
            assert marker not in repr(frame.f_locals)


def test_meta_validation自訂BaseException正規化且wrapper可讀取(monkeypatch):
    服務 = _建立()

    def 失敗(_結構):
        raise _結構基底錯誤("SCHEMA-SECRET")

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 失敗)
    with pytest.raises(ValueError, match="^發布值確認輸入無效$") as 錯誤:
        _確認(服務)
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None

    monkeypatch.undo()
    wrapper = 發布規劃服務(權限協調器(object()), 草稿服務=服務)
    確認 = wrapper.確認發布值(
        "owner", "draft-race", slug="safe", response_schema={"type": "string"},
        docs="文件", endpoint_limit=1, credential_limit=1, 現在=121,
    )
    assert wrapper.讀取已確認草稿("owner", "draft-race", 現在=122).發布確認 == 確認


def test_外部owner先授權拒絕不得用未來時間刪除草稿():
    服務 = _建立()
    with pytest.raises(草稿存取錯誤):
        服務.確認發布值(
            "other", "draft-race", slug="safe", response_schema={"type": "string"},
            docs="文件", endpoint_limit=1, credential_limit=1, 現在=999,
        )
    assert 服務.讀取草稿("owner", "draft-race", 現在=159).草稿識別碼 == "draft-race"


@pytest.mark.parametrize("情境", ["missing", "foreign", "expired", "stale"])
def test_確認初始拒絕清除所有生產frame且owner先於到期變更(monkeypatch, 情境):
    服務, 擁有者, 草稿識別碼, 標記 = _建立標記權威草稿()
    wrapper = 發布規劃服務(權限協調器(object()), 草稿服務=服務)
    驗證次數 = 0

    def 不應驗證(_結構):
        nonlocal 驗證次數
        驗證次數 += 1

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 不應驗證)
    呼叫擁有者, 呼叫草稿, 現在 = 擁有者, 草稿識別碼, 120
    if 情境 == "missing":
        呼叫草稿 = f"missing-{標記}"
    elif 情境 == "foreign":
        呼叫擁有者, 現在 = f"foreign-{標記}", 999
    elif 情境 == "expired":
        現在 = 160
    else:
        object.__setattr__(服務._草稿[草稿識別碼], "狀態", "stale")
    原留存 = 服務._草稿.get(草稿識別碼)

    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
        _標記確認(wrapper, 呼叫擁有者, 呼叫草稿, 標記, 現在)

    assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None
    assert 驗證次數 == 0
    _斷言P03生產frame已清除(錯誤.value, 標記)
    if 情境 == "expired":
        assert 服務._草稿 == {}
    else:
        assert 服務._草稿[草稿識別碼] is 原留存
    if 情境 == "foreign":
        assert 服務.讀取已確認草稿(擁有者, 草稿識別碼, 現在=159).發布確認 is not None


def test_共用存取helper固定拒絕亦清除權威草稿且不讓外部刪除():
    服務, 擁有者, 草稿識別碼, 標記 = _建立標記權威草稿()
    原留存 = 服務._草稿[草稿識別碼]

    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
        服務.讀取草稿(f"foreign-{標記}", 草稿識別碼, 現在=999)

    assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None
    assert "_取得有效草稿" in _斷言P03生產frame已清除(錯誤.value, 標記)
    with 服務._鎖, pytest.raises(草稿存取錯誤) as helper錯誤:
        服務._取得有效草稿_已鎖定(f"foreign-{標記}", 草稿識別碼, 999)
    assert "_取得有效草稿_已鎖定" in _斷言P03生產frame已清除(helper錯誤.value, 標記)
    assert 服務._草稿[草稿識別碼] is 原留存
    assert 服務.讀取已確認草稿(擁有者, 草稿識別碼, 現在=159).發布確認 is not None


def test_schema驗證阻塞後更新使確認CAS拒絕並清除所有快照(monkeypatch):
    服務, 擁有者, 草稿識別碼, 標記 = _建立標記權威草稿()
    wrapper = 發布規劃服務(權限協調器(object()), 草稿服務=服務)
    原驗證 = 綱要模組.Draft202012Validator.check_schema
    已進入, 可繼續 = threading.Event(), threading.Event()

    def 阻塞驗證(結構):
        assert 標記 in repr(結構)
        已進入.set()
        assert 可繼續.wait(2)
        return 原驗證(結構)

    monkeypatch.setattr(綱要模組.Draft202012Validator, "check_schema", 阻塞驗證)
    with concurrent.futures.ThreadPoolExecutor() as 執行器:
        future = 執行器.submit(_標記確認, wrapper, 擁有者, 草稿識別碼, 標記, 120)
        assert 已進入.wait(2)
        更新結果 = 服務.更新草稿(擁有者, 草稿識別碼, {"updated": 標記}, 現在=121)
        更新後留存 = 服務._草稿[草稿識別碼]
        可繼續.set()
        with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
            future.result(timeout=2)

    assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None
    _斷言P03生產frame已清除(錯誤.value, 標記)
    assert 服務._草稿[草稿識別碼] is 更新後留存
    assert 更新結果._世代 == 更新後留存._世代 == 1
    assert 更新後留存.發布確認 is None and 更新後留存.狀態 == "draft"
