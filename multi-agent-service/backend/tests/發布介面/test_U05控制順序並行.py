"""RUN U05：控制流程、階段順序與兩端點並行隔離。"""

from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib

import pytest

import 繁中代理.發布介面.執行期.執行器 as 執行器模組
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext
from 繁中代理.發布介面.執行期.工具版本庫 import 工具快照項目, 工具版本庫
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照, 模型設定快照
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行器, 發布執行快照,
    發布執行請求, 發布執行錯誤, 建立發布執行器, 計算技能套件雜湊,
)

標記 = "U05_CONTROL_OWNER_SECRET"

@pytest.fixture(autouse=True)
def _清理弱狀態():
    yield
    gc.collect()

class _呼叫物件:
    def __init__(self, 結果=None):
        self.結果, self.呼叫, self.錯誤 = 結果, [], None

    def _回傳(self, *參數, **命名參數):
        self.呼叫.append((參數, 命名參數))
        if self.錯誤 is not None:
            raise self.錯誤
        return self.結果

    def 取得發布執行快照(self, endpoint_version_id):
        return self._回傳(endpoint_version_id)

    def 載入服務帳戶上下文(self, service_account_id, endpoint_version_id, source):
        return self._回傳(service_account_id, endpoint_version_id, source)

    def 載入技能套件快照(self, endpoint_version_id, skill_bundle_hash, manifest_reference, source):
        return self._回傳(endpoint_version_id, skill_bundle_hash, manifest_reference, source)

    def 取得工具修訂(self, name, revision):
        return self._回傳(name, revision)

    def 產生發布回應(self, **參數):
        結果 = self._回傳(**參數)
        return 結果 if 結果 is not None else 模型回應快照("完成", "stop", {}, [])


def _材料(尾碼="1", *, 有工具=True):
    版本, 帳戶 = f"ver-{尾碼}", f"sa-{尾碼}"
    內容 = f"套件-{尾碼}".encode()
    檔案 = (技能套件檔案(path="SKILL.md", sha256=hashlib.sha256(內容).hexdigest(), content=內容),)
    套件雜湊 = 計算技能套件雜湊(檔案)
    工具庫, 工具們, 名稱 = 工具版本庫(), (), f"tool-{尾碼}"
    if 有工具:
        工具們 = (工具庫.登錄修訂(f"rev-{尾碼}", 工具定義(名稱, f"說明-{尾碼}", {}, lambda _: 尾碼)),)
    快照 = 發布執行快照(
        endpoint_id=f"endpoint-{尾碼}", version_id=版本, service_account_id=帳戶,
        system_prompt=f"prompt-{尾碼}", permission_snapshot_digest=尾碼[0] * 64,
        skill_bundle_hash=套件雜湊, tool_handler_release=f"release-{尾碼}",
        tool_snapshot=工具們, model_config=模型設定快照(f"fake-{尾碼}", f"model-{尾碼}", 0, 100, 5, False, 1),
        response_schema=None, manifest_reference=f"bundle-{尾碼}/manifest.json",
    )
    上下文 = ServiceAccountContext(
        service_account_id=帳戶, endpoint_version_id=版本,
        permission_snapshot_digest=尾碼[0] * 64,
        allowed_tools=(() if not 有工具 else (名稱,)), skill_bundle_hash=套件雜湊,
        tool_handler_release=f"release-{尾碼}",
    )
    套件 = 技能套件快照(endpoint_version_id=版本, skill_bundle_hash=套件雜湊,
                      manifest_digest=hashlib.sha256(b"{}").hexdigest(),
                      清單原始資料=b"{}", files=檔案)
    return 快照, 上下文, 套件, 工具庫


def _元件(材料=None):
    快照, 上下文, 套件, 工具庫 = 材料 or _材料()
    版本, 帳戶, 套件源, 模型 = _呼叫物件(快照), _呼叫物件(上下文), _呼叫物件(套件), _呼叫物件()
    return (快照, 上下文, 套件, 工具庫), (版本, 帳戶, 套件源, 模型)


def _建立(材料, 元件):
    快照, _, _, 工具庫 = 材料
    版本, 帳戶, 套件源, 模型 = 元件
    return 建立發布執行器(
        endpoint_version_id=快照.version_id, service_account_id=快照.service_account_id,
        發布快照提供者=版本, 服務帳戶載入器=帳戶, 技能套件載入器=套件源,
        工具修訂提供者=工具庫, 模型供應商註冊表={快照.model_config.provider: 模型},
    )


def _含標記(值, 已看):
    if id(值) in 已看:
        return False
    已看.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if isinstance(值, BaseException):
        return _含標記(值.args, 已看)
    if type(值) is dict:
        return any(_含標記(鍵, 已看) or _含標記(子值, 已看) for 鍵, 子值 in 值.items())
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(子值, 已看) for 子值 in 值)
    if hasattr(值, "__self__") and _含標記(object.__getattribute__(值, "__self__"), 已看):
        return True
    if hasattr(值, "__dict__") and _含標記(vars(值), 已看):
        return True
    for 類別 in type(值).__mro__:
        欄位們 = 類別.__dict__.get("__slots__", ())
        for 欄位 in (欄位們,) if type(欄位們) is str else 欄位們:
            try:
                if _含標記(object.__getattribute__(值, 欄位), 已看):
                    return True
            except (AttributeError, TypeError):
                pass
    return False


def _執行器框架已清理(錯誤):
    數量, 追蹤 = 0, 錯誤.__traceback__
    while 追蹤:
        if 追蹤.tb_frame.f_code.co_filename.endswith("執行器.py"):
            數量 += 1
            assert not any(_含標記(值, set()) for 值 in 追蹤.tb_frame.f_locals.values()), 追蹤.tb_frame.f_code.co_name
        追蹤 = 追蹤.tb_next
    assert 數量


控制型別 = [KeyboardInterrupt, SystemExit, GeneratorExit]
控制型別 += [type(f"子類{i}", (類別,), {}) for i, 類別 in enumerate(控制型別)]
控制型別 += [type("再一層子類", (控制型別[-1],), {})]


@pytest.mark.parametrize("階段", ["version", "sa", "bundle", "tool", "model"])
@pytest.mark.parametrize("例外型別", 控制型別)
def test_各callback控制流程原型通過且所有執行器框架清理(階段, 例外型別):
    材料, 元件 = _元件()
    版本, 帳戶, 套件源, 模型 = 元件
    目標 = {"version": 版本, "sa": 帳戶, "bundle": 套件源, "tool": 材料[3], "model": 模型}[階段]
    中斷 = 例外型別(標記)
    if 階段 == "tool":
        目標.取得工具修訂 = lambda *_: (_ for _ in ()).throw(中斷)
    else:
        目標.錯誤 = 中斷
    with pytest.raises(例外型別) as 錯誤:
        執行器 = _建立(材料, 元件)
        執行器.執行(發布執行請求({"secret": 標記}))
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    _執行器框架已清理(錯誤.value)


@pytest.mark.parametrize("階段", ["version", "sa", "bundle", "tool", "model"])
@pytest.mark.parametrize("例外型別", [RuntimeError, type("惡意錯誤", (BaseException,), {})])
def test_各callback普通與自訂BaseException皆固定無鏈且模型不越階段(階段, 例外型別):
    材料, 元件 = _元件()
    目標 = {"version": 元件[0], "sa": 元件[1], "bundle": 元件[2], "tool": 材料[3], "model": 元件[3]}[階段]
    if 階段 == "tool":
        目標.取得工具修訂 = lambda *_: (_ for _ in ()).throw(例外型別(標記))
    else:
        目標.錯誤 = 例外型別(標記)
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        執行器 = _建立(材料, 元件)
        執行器.執行(發布執行請求({"secret": 標記}))
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert len(元件[3].呼叫) == (1 if 階段 == "model" else 0)
    _執行器框架已清理(錯誤.value)


@pytest.mark.parametrize("名稱,入口", [
    ("_重建發布快照", "factory"), ("_重建技能套件", "factory"),
    ("_重建套件檔案", "factory"), ("_重建工具快照", "factory"),
    ("_建立提示", "factory"), ("_捕捉模型註冊表", "factory"),
    ("_解析正規JSON", "execute"), ("_建立正規JSON", "request"),
])
def test_內部重建提示與嚴格JSON控制流程逐層原樣清理(monkeypatch, 名稱, 入口):
    材料, 元件 = _元件()
    中斷 = KeyboardInterrupt(標記)
    if 入口 == "execute":
        執行器 = _建立(材料, 元件)
    monkeypatch.setattr(執行器模組, 名稱, lambda *_: (_ for _ in ()).throw(中斷))
    with pytest.raises(KeyboardInterrupt) as 錯誤:
        if 入口 == "request":
            發布執行請求({"secret": 標記})
        elif 入口 == "execute":
            執行器.執行(發布執行請求({"secret": 標記}))
        else:
            _建立(材料, 元件)
    assert 錯誤.value is 中斷
    _執行器框架已清理(錯誤.value)


@pytest.mark.parametrize("模式", ["version-id", "sa", "bundle", "tool", "model-provider"])
def test_階段失敗不呼叫後續provider(模式):
    材料, 元件 = _元件()
    if 模式 == "version-id":
        object.__setattr__(材料[0], "version_id", "ver-other")
    elif 模式 == "sa":
        object.__setattr__(元件[1].結果, "permission_snapshot_digest", "f" * 64)
    elif 模式 == "bundle":
        object.__setattr__(元件[2].結果, "manifest_digest", "f" * 64)
    elif 模式 == "tool":
        object.__setattr__(材料[0].tool_snapshot[0], "digest", "f" * 64)
    with pytest.raises(發布執行錯誤):
        if 模式 == "model-provider":
            建立發布執行器(endpoint_version_id="ver-1", service_account_id="sa-1",
                發布快照提供者=元件[0], 服務帳戶載入器=元件[1], 技能套件載入器=元件[2],
                工具修訂提供者=材料[3], 模型供應商註冊表={})
        else:
            if 模式 == "version-id":
                建立發布執行器(endpoint_version_id="ver-1", service_account_id="sa-1",
                    發布快照提供者=元件[0], 服務帳戶載入器=元件[1], 技能套件載入器=元件[2],
                    工具修訂提供者=材料[3], 模型供應商註冊表={"fake-1": 元件[3]})
            else:
                _建立(材料, 元件)
    版本數, 帳戶數, 套件數, 模型數 = map(lambda 項: len(項.呼叫), 元件)
    assert (帳戶數, 套件數, 模型數) == {
        "version-id": (0, 0, 0), "sa": (1, 0, 0), "bundle": (1, 1, 0),
        "tool": (1, 1, 0), "model-provider": (1, 1, 0),
    }[模式]
    assert 版本數 == 1


def test_零工具可執行且兩端點並行建立執行完全隔離():
    def 工作(尾碼, 有工具=True):
        材料, 元件 = _元件(_材料(尾碼, 有工具=有工具))
        回應 = _建立(材料, 元件).執行(發布執行請求({"input": 尾碼}))
        呼叫 = 元件[3].呼叫[0][1]
        return 回應.text, 呼叫["messages"], 呼叫["tools"]
    with ThreadPoolExecutor(max_workers=3) as 池:
        甲, 乙, 空 = list(池.map(lambda 參數: 工作(*參數), [("1", True), ("2", True), ("3", False)]))
    for 尾碼, 結果 in (("1", 甲), ("2", 乙), ("3", 空)):
        assert 結果[0] == "完成" and f"prompt-{尾碼}" in 結果[1][0]["content"]
        assert 結果[1][1]["metadata"]["input_json"] == {"input": 尾碼}
        assert [項["function"]["name"] for 項 in 結果[2]] == ([] if 尾碼 == "3" else [f"tool-{尾碼}"])
        assert all(f"prompt-{其他}" not in repr(結果) for 其他 in "123" if 其他 != 尾碼)


def test_object_new偽造公開私有與subclass皆在模型前拒絕且不可加狀態():
    _, 元件 = _元件()
    class 子類(發布執行器):
        __slots__ = ()
    for 偽造 in (object.__new__(發布執行器), object.__new__(執行器模組._發布執行器實作), object.__new__(子類)):
        with pytest.raises(發布執行錯誤):
            偽造.執行(發布執行請求({"x": 1}))
        with pytest.raises(AttributeError):
            object.__setattr__(偽造, "state", 標記)
    assert 元件[3].呼叫 == []
