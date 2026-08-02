from dataclasses import FrozenInstanceError, fields

import pytest

import 繁中代理.發布介面.執行期.服務帳戶 as 服務帳戶模組
from 繁中代理.發布介面.執行期.服務帳戶 import (
    ServiceAccountContext, 服務帳戶上下文錯誤, 載入服務帳戶上下文或失敗關閉,
)

MARKER = "OWNER_SECRET_MARKER"


def _上下文():
    return ServiceAccountContext(
        service_account_id="sa-1",
        endpoint_version_id="ver-2",
        permission_snapshot_digest="a" * 64,
        allowed_tools=("read_snapshot",),
        skill_bundle_hash="b" * 64,
        tool_handler_release="release-7",
    )


def _含標記(值, 已看=None):
    """以不呼叫被測物件方法的方式掃描測試已知容器。"""
    已看 = set() if 已看 is None else 已看
    if id(值) in 已看:
        return False
    已看.add(id(值))
    if type(值) is str:
        return MARKER in 值
    if isinstance(值, BaseException):
        return _含標記(值.args, 已看)
    if type(值) in (tuple, list, dict):
        子值 = 值.values() if type(值) is dict else 值
        return any(_含標記(項目, 已看) for 項目 in 子值)
    try:
        屬性 = object.__getattribute__(值, "__dict__")
    except (AttributeError, TypeError):
        return False
    return _含標記(屬性, 已看)


def _生產框架不含標記(例外):
    """只檢查服務帳戶邊界的 traceback locals，不觸碰 adapter 框架。"""
    框架 = []
    追蹤 = 例外.__traceback__
    while 追蹤 is not None:
        if 追蹤.tb_frame.f_code.co_filename.endswith("服務帳戶.py"):
            框架.append(追蹤.tb_frame.f_code.co_name)
            for 值 in 追蹤.tb_frame.f_locals.values():
                assert not _含標記(值)
        追蹤 = 追蹤.tb_next
    assert 框架
    return 框架


class _載入器:
    def __init__(self, 結果=None, 錯誤=None):
        self.結果 = 結果
        self.錯誤 = 錯誤
        self.呼叫 = []

    def 載入服務帳戶上下文(self, service_account_id, endpoint_version_id, source):
        self.呼叫.append((service_account_id, endpoint_version_id, source))
        if self.錯誤:
            raise self.錯誤
        return self.結果
def test_ServiceAccountContext是exact_frozen_DTO且無owner欄位():
    上下文 = _上下文()
    assert type(上下文) is ServiceAccountContext
    assert [欄位.name for 欄位 in fields(上下文)] == [
        "service_account_id", "endpoint_version_id", "permission_snapshot_digest",
        "allowed_tools", "skill_bundle_hash", "tool_handler_release",
    ]
    with pytest.raises(FrozenInstanceError):
        上下文.allowed_tools = ()
    assert not hasattr(上下文, "__dict__")
    with pytest.raises(AttributeError):
        object.__setattr__(上下文, "owner_memory", MARKER)
    for 名稱 in ("owner_user_id", "memory", "session", "global_context", "workdir"):
        assert not hasattr(上下文, 名稱)


@pytest.mark.parametrize(
    "覆寫",
    [
        {"service_account_id": True},
        {"permission_snapshot_digest": "x" * 64},
        {"allowed_tools": ["read_snapshot"]},
        {"allowed_tools": ("read_snapshot", "read_snapshot")},
        {"skill_bundle_hash": "b" * 63},
        {"tool_handler_release": ""},
    ],
)
def test_ServiceAccountContext拒絕非exact欄位型別與內容(覆寫):
    資料 = {
        "service_account_id": "sa-1", "endpoint_version_id": "ver-2",
        "permission_snapshot_digest": "a" * 64, "allowed_tools": ("read_snapshot",),
        "skill_bundle_hash": "b" * 64, "tool_handler_release": "release-7",
    }
    資料.update(覆寫)
    with pytest.raises(服務帳戶上下文錯誤, match="^發布服務帳戶上下文無效$"):
        ServiceAccountContext(**資料)


@pytest.mark.parametrize(
    ("服務帳戶識別碼", "端點版本識別碼", "來源"),
    [
        ("sa-1", "ver-2", f"owner_memory/{MARKER}"),
        (f"bad/{MARKER}", "ver-2", "endpoint_version_snapshot"),
        ("sa-1", f"bad/{MARKER}", "endpoint_version_snapshot"),
    ],
)
def test_不可信預檢請求在任何adapter副作用前清理失敗關閉(
    服務帳戶識別碼, 端點版本識別碼, 來源
):
    """禁止來源與 malformed ID 不可呼叫 adapter 或留在生產 traceback。"""
    載入器 = _載入器(_上下文())
    object.__setattr__(載入器, "owner_marker", MARKER)
    with pytest.raises(服務帳戶上下文錯誤) as 錯誤:
        載入服務帳戶上下文或失敗關閉(
            載入器, 服務帳戶識別碼, 端點版本識別碼, source=來源
        )
    assert str(錯誤.value) == "發布服務帳戶上下文不可用"
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    assert 載入器.呼叫 == []
    assert _生產框架不含標記(錯誤.value) == ["載入服務帳戶上下文或失敗關閉"]


def test_只從端點版本快照載入且拒絕adapter偽造DTO():
    正常 = _載入器(_上下文())
    載入結果 = 載入服務帳戶上下文或失敗關閉(正常, "sa-1", "ver-2")
    assert 載入結果 == 正常.結果
    assert 載入結果 is not 正常.結果
    assert 正常.呼叫 == [("sa-1", "ver-2", "endpoint_version_snapshot")]

    class 偽造(ServiceAccountContext):
        pass

    偽造載入器 = _載入器(object.__new__(偽造))
    with pytest.raises(服務帳戶上下文錯誤, match="^發布服務帳戶上下文不可用$"):
        載入服務帳戶上下文或失敗關閉(偽造載入器, "sa-1", "ver-2")
    assert len(偽造載入器.呼叫) == 1


@pytest.mark.parametrize("缺少欄位", [欄位.name for 欄位 in fields(ServiceAccountContext)])
def test_adapter回傳缺欄exact物件時固定失敗且不執行動態存取(缺少欄位):
    """object.__new__ 可略過建構子，但 loader 必須完整重建驗證。"""
    偽造 = object.__new__(ServiceAccountContext)
    for 欄位 in fields(ServiceAccountContext):
        if 欄位.name != 缺少欄位:
            object.__setattr__(偽造, 欄位.name, getattr(_上下文(), 欄位.name))
    with pytest.raises(服務帳戶上下文錯誤, match="^發布服務帳戶上下文不可用$"):
        載入服務帳戶上下文或失敗關閉(_載入器(偽造), "sa-1", "ver-2")


def test_adapter回傳遭竄改exact物件時清除owner標記():
    """即使 exact frozen DTO 遭 object.__setattr__ 竄改也不可直接信任。"""
    偽造 = _上下文()
    object.__setattr__(偽造, "allowed_tools", (MARKER, MARKER))
    with pytest.raises(服務帳戶上下文錯誤) as 錯誤:
        載入服務帳戶上下文或失敗關閉(_載入器(偽造), "sa-1", "ver-2")
    assert str(錯誤.value) == "發布服務帳戶上下文不可用"
    _生產框架不含標記(錯誤.value)


def test_adapter回傳敵意欄位時不執行其accessor():
    """重建驗證只看 exact 型別，不呼叫 forged 欄位的方法。"""
    存取 = []

    class 敵意欄位:
        def __getattribute__(self, 名稱):
            存取.append(名稱)
            raise AssertionError("不可執行敵意 accessor")

    偽造 = _上下文()
    object.__setattr__(偽造, "service_account_id", 敵意欄位())
    with pytest.raises(服務帳戶上下文錯誤, match="^發布服務帳戶上下文不可用$"):
        載入服務帳戶上下文或失敗關閉(_載入器(偽造), "sa-1", "ver-2")
    assert 存取 == []


@pytest.mark.parametrize("例外型別", [RuntimeError, type("惡意例外", (BaseException,), {})])
def test_adapter嘗試fallback或洩漏owner例外時回固定清理錯誤(例外型別):
    載入器 = _載入器(錯誤=例外型別(MARKER))
    with pytest.raises(服務帳戶上下文錯誤) as 錯誤:
        載入服務帳戶上下文或失敗關閉(載入器, "sa-1", "ver-2")
    assert str(錯誤.value) == "發布服務帳戶上下文不可用"
    assert MARKER not in repr(錯誤.value)
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _生產框架不含標記(錯誤.value)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_adapter控制流程例外維持原型別(例外型別):
    with pytest.raises(例外型別) as 錯誤:
        載入服務帳戶上下文或失敗關閉(
            _載入器(錯誤=例外型別(MARKER)), "sa-1", "ver-2"
        )
    assert type(錯誤.value) is 例外型別
    assert 錯誤.value.args == (MARKER,)
    _生產框架不含標記(錯誤.value)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_allowed_tools受信驗證控制流程例外逐層清除敏感locals(monkeypatch, 例外型別):
    不可信 = ServiceAccountContext(
        service_account_id="sa-1", endpoint_version_id="ver-2",
        permission_snapshot_digest="a" * 64, allowed_tools=(MARKER,),
        skill_bundle_hash="b" * 64, tool_handler_release="release-7",
    )
    原驗證器 = 服務帳戶模組._是識別碼
    呼叫數 = 0

    def 受控驗證器(值):
        nonlocal 呼叫數
        呼叫數 += 1
        if 呼叫數 == 5:
            raise 例外型別(MARKER)
        return 原驗證器(值)

    monkeypatch.setattr(服務帳戶模組, "_是識別碼", 受控驗證器)
    with pytest.raises(例外型別) as 錯誤:
        載入服務帳戶上下文或失敗關閉(_載入器(不可信), "sa-1", "ver-2")
    assert type(錯誤.value) is 例外型別
    assert 錯誤.value.args == (MARKER,)
    assert 呼叫數 == 5
    assert {
        "載入服務帳戶上下文或失敗關閉", "_正規化上下文", "__init__", "_工具名稱皆合法"
    } <= set(
        _生產框架不含標記(錯誤.value)
    )
