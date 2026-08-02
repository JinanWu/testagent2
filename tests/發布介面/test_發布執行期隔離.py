"""發布執行期服務帳戶與 owner 資料隔離測試。"""

from dataclasses import FrozenInstanceError, fields

import pytest

import 繁中代理.發布介面.執行期.服務帳戶 as 服務帳戶模組
from 繁中代理.發布介面.執行期.服務帳戶 import (
    ServiceAccountContext,
    服務帳戶上下文錯誤,
    載入服務帳戶上下文或失敗關閉,
)
from 繁中代理.發布介面.執行期.工具版本庫 import 工具版本庫
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照, 模型設定快照
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行快照, 發布執行請求,
    發布執行錯誤, 建立發布執行器, 計算技能套件雜湊,
)
from 繁中代理.工具 import 工具定義
from 繁中代理.代理執行階段 import (
    發布執行階段相容轉接器, 發布相容轉接錯誤,
    建立發布執行階段相容轉接器,
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


class _版本提供者:
    def __init__(self, 快照):
        self.快照, self.呼叫 = 快照, []

    def 取得發布執行快照(self, endpoint_version_id):
        self.呼叫.append(endpoint_version_id)
        return self.快照


class _套件載入器:
    def __init__(self, 快照):
        self.快照, self.呼叫 = 快照, []

    def 載入技能套件快照(self, endpoint_version_id, skill_bundle_hash, manifest_reference, source):
        self.呼叫.append((endpoint_version_id, skill_bundle_hash, manifest_reference, source))
        return self.快照


class _模型:
    def __init__(self, 回傳="完成"):
        self.回傳, self.呼叫 = 回傳, []

    def 產生發布回應(self, **參數):
        self.呼叫.append(參數)
        return 模型回應快照(self.回傳, "stop", {"total": 1}, [])


def _執行材料(*, 端點="endpoint-1", 版本="ver-2", 帳戶="sa-1", 提示="固定系統提示"):
    import hashlib
    原檔案 = (
        ("SKILL.md", "技能主文".encode()),
        ("assets/secret.bin", b"BINARY_SECRET"),
        ("references/guide.md", "參考內容".encode()),
    )
    檔案 = tuple(
        技能套件檔案(path=路徑, sha256=hashlib.sha256(內容).hexdigest(), content=內容)
        for 路徑, 內容 in 原檔案
    )
    套件雜湊 = 計算技能套件雜湊(檔案)
    套件 = 技能套件快照(
        endpoint_version_id=版本, skill_bundle_hash=套件雜湊,
        manifest_digest=套件雜湊, files=檔案,
    )
    工具庫 = 工具版本庫()
    工具項目 = 工具庫.登錄修訂(
        "rev-1", 工具定義("lookup", "固定工具", {"type": "object"}, lambda 參數: "舊處理器")
    )
    版本快照 = 發布執行快照(
        endpoint_id=端點, version_id=版本, service_account_id=帳戶,
        system_prompt=提示, permission_snapshot_digest="a" * 64,
        skill_bundle_hash=套件雜湊, tool_handler_release="release-7",
        tool_snapshot=(工具項目,),
        model_config=模型設定快照("fake", "model-1", 0, 100, 5, False, 1),
        response_schema=None, manifest_reference="manifest-1",
    )
    上下文 = ServiceAccountContext(
        service_account_id=帳戶, endpoint_version_id=版本,
        permission_snapshot_digest="a" * 64, allowed_tools=("lookup",),
        skill_bundle_hash=套件雜湊, tool_handler_release="release-7",
    )
    return 版本快照, 上下文, 套件, 工具庫


def _建立測試執行器(材料=None):
    版本快照, 上下文, 套件, 工具庫 = 材料 or _執行材料()
    版本提供者, 帳戶載入器 = _版本提供者(版本快照), _載入器(上下文)
    套件載入器, 模型 = _套件載入器(套件), _模型()
    執行器 = 建立發布執行器(
        endpoint_version_id=版本快照.version_id,
        service_account_id=版本快照.service_account_id,
        發布快照提供者=版本提供者, 服務帳戶載入器=帳戶載入器,
        技能套件載入器=套件載入器, 工具修訂提供者=工具庫,
        模型供應商註冊表={"fake": 模型},
    )
    return 執行器, 版本提供者, 帳戶載入器, 套件載入器, 模型


def test_相容轉接器明確opt_in且只傳一則發布JSON():
    執行器, *_, 模型 = _建立測試執行器()
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    輸入 = {"question": "你好", "nested": [1]}
    回應 = 轉接器.執行發布輸入(輸入)
    輸入["nested"].append(MARKER)
    assert type(回應) is 模型回應快照 and 回應.text == "完成"
    assert 模型.呼叫[0]["messages"][1] == {
        "role": "user", "content": '{"nested":[1],"question":"你好"}',
        "metadata": {"input_json": {"question": "你好", "nested": [1]}},
    }


def test_相容轉接器不接觸舊owner_session_tool_workdir能力(monkeypatch):
    import importlib
    模組 = importlib.import_module("繁中代理.代理執行階段")
    執行器, *_ = _建立測試執行器()
    碰觸 = []
    def 禁止(*_參數, **_命名參數):
        碰觸.append(True)
        raise AssertionError(MARKER)
    for 名稱 in ("建立預設工具登錄器", "建立技能索引摘要", "建立預設使用者上下文"):
        monkeypatch.setattr(模組, 名稱, 禁止)
    monkeypatch.setattr(模組.代理執行階段, "建立系統提示詞", 禁止)
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    assert 轉接器.執行發布輸入({"ok": True}).text == "完成"
    assert 碰觸 == []
    for 名稱 in ("owner", "session", "memory", "workdir", "skills", "tools", "provider", "__dict__"):
        assert not hasattr(轉接器, 名稱)


def test_相容轉接器捕捉原始方法且拒絕偽造與子類(monkeypatch):
    import importlib
    模組 = importlib.import_module("繁中代理.代理執行階段")
    執行器, *_ = _建立測試執行器()
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    monkeypatch.setattr(type(執行器), "執行", lambda *_: (_ for _ in ()).throw(AssertionError(MARKER)))
    assert 轉接器.執行發布輸入({"pinned": True}).text == "完成"
    class 子類(發布執行階段相容轉接器):
        pass
    for 偽造 in (object.__new__(發布執行階段相容轉接器), object.__new__(模組._發布相容轉接器實作), object.__new__(子類)):
        with pytest.raises(發布相容轉接錯誤, match="^發布相容轉接不可用$"):
            偽造.執行發布輸入({"x": 1})


@pytest.mark.parametrize("輸入", [lambda: {"cycle": None}, lambda: {"huge": "x" * 500_001}])
def test_相容轉接器malformed輸入在模型前固定拒絕(輸入):
    執行器, *_, 模型 = _建立測試執行器()
    值 = 輸入()
    if "cycle" in 值:
        值["cycle"] = 值
    with pytest.raises(發布相容轉接錯誤, match="^發布相容轉接不可用$") as 錯誤:
        建立發布執行階段相容轉接器(執行器).執行發布輸入(值)
    assert 模型.呼叫 == []
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_相容轉接器平行輸入不交叉污染():
    from concurrent.futures import ThreadPoolExecutor
    執行器, *_, 模型 = _建立測試執行器()
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    with ThreadPoolExecutor(max_workers=8) as 池:
        回應 = list(池.map(轉接器.執行發布輸入, ({"n": i} for i in range(24))))
    assert len(回應) == 24
    assert {呼叫["messages"][1]["metadata"]["input_json"]["n"] for 呼叫 in 模型.呼叫} == set(range(24))


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_相容轉接器目標控制流程維持identity且清除框架(例外型別):
    import importlib
    模組 = importlib.import_module("繁中代理.代理執行階段")
    執行器, *_ = _建立測試執行器()
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    例外 = 例外型別(MARKER)
    def 目標(_請求):
        raise 例外
    with 模組._發布相容狀態鎖:
        原狀態 = 模組._發布相容狀態[轉接器]
        模組._發布相容狀態[轉接器] = (原狀態[0], 目標, 原狀態[2])
    with pytest.raises(例外型別) as 錯誤:
        轉接器.執行發布輸入({"marker": MARKER})
    assert 錯誤.value is 例外 and 錯誤.value.args == (MARKER,)
    追蹤 = 錯誤.value.__traceback__
    while 追蹤:
        if 追蹤.tb_frame.f_code.co_filename.endswith("代理執行階段.py"):
            for 值 in 追蹤.tb_frame.f_locals.values():
                assert not _含標記(值)
        追蹤 = 追蹤.tb_next


@pytest.mark.parametrize("例外型別", [RuntimeError, type("惡意相容例外", (BaseException,), {})])
def test_相容轉接器目標失敗固定且不fallback(例外型別):
    import importlib
    模組 = importlib.import_module("繁中代理.代理執行階段")
    執行器, *_ = _建立測試執行器()
    轉接器 = 建立發布執行階段相容轉接器(執行器)
    呼叫 = []
    def 目標(_請求):
        呼叫.append(True)
        raise 例外型別(MARKER)
    with 模組._發布相容狀態鎖:
        原狀態 = 模組._發布相容狀態[轉接器]
        模組._發布相容狀態[轉接器] = (原狀態[0], 目標, 原狀態[2])
    with pytest.raises(發布相容轉接錯誤, match="^發布相容轉接不可用$") as 錯誤:
        轉接器.執行發布輸入({"marker": MARKER})
    assert 呼叫 == [True]
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_執行器只組裝版本提示套件與一則不可信使用者JSON():
    執行器, 版本提供者, 帳戶載入器, 套件載入器, 模型 = _建立測試執行器()
    輸入 = {"question": "你好", "nested": [1, True]}
    回應 = 執行器.執行(發布執行請求(輸入))
    輸入["question"] = MARKER

    assert 回應.text == "完成"
    assert 版本提供者.呼叫 == ["ver-2"]
    assert 帳戶載入器.呼叫 == [("sa-1", "ver-2", "endpoint_version_snapshot")]
    assert 套件載入器.呼叫[0][3] == "endpoint_version_snapshot"
    訊息 = 模型.呼叫[0]["messages"]
    assert 訊息[0] == {
        "role": "system", "content": "固定系統提示\n\n## 技能套件：SKILL.md\n技能主文\n\n## 技能套件：references/guide.md\n參考內容"
    }
    assert 訊息[1]["role"] == "user"
    assert 訊息[1]["metadata"]["input_json"] == {"question": "你好", "nested": [1, True]}
    assert "BINARY_SECRET" not in repr(訊息)
    assert 模型.呼叫[0]["tools"][0]["function"]["name"] == "lookup"


def test_建立點封存版本套件工具與模型方法():
    材料 = _執行材料()
    執行器, 版本提供者, _, 套件載入器, 模型 = _建立測試執行器(材料)
    object.__setattr__(材料[0], "system_prompt", "LIVE_PROMPT")
    object.__setattr__(材料[2], "files", ())
    版本提供者.取得發布執行快照 = lambda _: (_ for _ in ()).throw(AssertionError())
    套件載入器.載入技能套件快照 = lambda *_: (_ for _ in ()).throw(AssertionError())
    模型.產生發布回應 = lambda **_: (_ for _ in ()).throw(AssertionError())
    材料[3].移除修訂("lookup", "rev-1")

    assert 執行器.執行(發布執行請求({"x": 1})).text == "完成"
    assert 模型.呼叫[0]["messages"][0]["content"].startswith("固定系統提示")


@pytest.mark.parametrize(
    "欄位,值",
    [
        ("service_account_id", "sa-other"), ("version_id", "ver-other"),
        ("permission_snapshot_digest", "c" * 64), ("skill_bundle_hash", "d" * 64),
        ("tool_handler_release", "release-other"), ("tool_snapshot", ()),
    ],
)
def test_版本與服務帳戶交叉欄位不符在套件工具模型前拒絕(欄位, 值):
    版本快照, 上下文, 套件, 工具庫 = _執行材料()
    object.__setattr__(版本快照, 欄位, 值)
    套件載入器, 模型 = _套件載入器(套件), _模型()
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        建立發布執行器(
            endpoint_version_id="ver-2", service_account_id="sa-1",
            發布快照提供者=_版本提供者(版本快照), 服務帳戶載入器=_載入器(上下文),
            技能套件載入器=套件載入器, 工具修訂提供者=工具庫,
            模型供應商註冊表={"fake": 模型},
        )
    assert 套件載入器.呼叫 == [] and 模型.呼叫 == []
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


@pytest.mark.parametrize("路徑", ["../SKILL.md", "/SKILL.md", "a\\b.md", "e\u0301.md", "a//b.md"])
def test_技能套件拒絕非正規POSIX_NFC路徑(路徑):
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        技能套件檔案(path=路徑, sha256="a" * 64, content=b"x")


def test_執行器為factory_sealed且沒有owner_live能力():
    執行器, *_ = _建立測試執行器()
    for 名稱 in ("memory", "session", "workdir", "owner", "skills", "__dict__"):
        assert not hasattr(執行器, 名稱)
    with pytest.raises(發布執行錯誤):
        type(執行器)()
    with pytest.raises(AttributeError):
        object.__setattr__(執行器, "prompt", MARKER)


def test_執行不讀取cwd_home_open或環境(monkeypatch):
    執行器, *_, 模型 = _建立測試執行器()
    def 禁止(*_參數, **_命名參數):
        raise AssertionError(MARKER)
    import builtins
    from pathlib import Path
    import os
    monkeypatch.setattr(os, "getcwd", 禁止)
    monkeypatch.setattr(Path, "home", 禁止)
    monkeypatch.setattr(builtins, "open", 禁止)
    monkeypatch.setenv("U05_OWNER_SECRET", MARKER)
    assert 執行器.執行(發布執行請求({"ok": True})).text == "完成"
    assert MARKER not in repr(模型.呼叫)


def test_請求與response_schema只保存private_canonical且每次fresh():
    請求 = 發布執行請求({"nested": [1]})
    第一次 = 請求.input
    第一次["nested"].append(2)
    assert 請求.input == {"nested": [1]}
    assert object.__getattribute__(請求, "input") is not object.__getattribute__(請求, "input")
    assert not hasattr(請求, "__dict__")
    assert object.__getattribute__(請求, "_input_json") == '{"nested":[1]}'

    版本, _, _, _ = _執行材料()
    結構 = {"type": "object", "properties": {"answer": {"type": "string"}}}
    結構版本 = 發布執行快照(
        endpoint_id=版本.endpoint_id, version_id=版本.version_id,
        service_account_id=版本.service_account_id, system_prompt=版本.system_prompt,
        permission_snapshot_digest=版本.permission_snapshot_digest,
        skill_bundle_hash=版本.skill_bundle_hash,
        tool_handler_release=版本.tool_handler_release, tool_snapshot=版本.tool_snapshot,
        model_config=模型設定快照("fake", "model-1", 0, 100, 5, True, 1),
        response_schema=結構, manifest_reference=版本.manifest_reference,
    )
    結構["type"] = "array"
    第一份 = 結構版本.response_schema
    第一份["type"] = "array"
    assert 結構版本.response_schema["type"] == "object"
    assert object.__getattribute__(結構版本, "response_schema") is not 第一份
    assert type(object.__getattribute__(結構版本, "_response_schema_json")) is str


@pytest.mark.parametrize("偽造原文", ['{"x":1, "y":2}', '{"x":1,"x":2}', '{"x":NaN}'])
def test_偽造請求private原文在模型前失敗關閉(偽造原文):
    執行器, *_, 模型 = _建立測試執行器()
    請求 = 發布執行請求({"x": 1})
    object.__setattr__(請求, "_input_json", 偽造原文)
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        執行器.執行(請求)
    assert 模型.呼叫 == []
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_偽造response_schema_private原文在SA前拒絕():
    版本, 上下文, 套件, 工具庫 = _執行材料()
    object.__setattr__(版本, "_response_schema_json", '{"type": "object"}')
    帳戶載入器 = _載入器(上下文)
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        建立發布執行器(
            endpoint_version_id="ver-2", service_account_id="sa-1",
            發布快照提供者=_版本提供者(版本), 服務帳戶載入器=帳戶載入器,
            技能套件載入器=_套件載入器(套件), 工具修訂提供者=工具庫,
            模型供應商註冊表={"fake": _模型()},
        )
    assert 帳戶載入器.呼叫 == []


def test_所有callback方法在第一個callback前捕捉且後續竄改無效():
    版本, 上下文, 套件, 工具庫 = _執行材料()
    帳戶載入器, 套件載入器, 模型 = _載入器(上下文), _套件載入器(套件), _模型()
    註冊表 = {"fake": 模型}

    class 惡意版本提供者(_版本提供者):
        def 取得發布執行快照(self, endpoint_version_id):
            帳戶載入器.載入服務帳戶上下文 = lambda *_: (_ for _ in ()).throw(AssertionError())
            套件載入器.載入技能套件快照 = lambda *_: (_ for _ in ()).throw(AssertionError())
            工具庫.取得工具修訂 = lambda *_: (_ for _ in ()).throw(AssertionError())
            模型.產生發布回應 = lambda **_: (_ for _ in ()).throw(AssertionError())
            註冊表["fake"] = object()
            return super().取得發布執行快照(endpoint_version_id)

    執行器 = 建立發布執行器(
        endpoint_version_id="ver-2", service_account_id="sa-1",
        發布快照提供者=惡意版本提供者(版本), 服務帳戶載入器=帳戶載入器,
        技能套件載入器=套件載入器, 工具修訂提供者=工具庫,
        模型供應商註冊表=註冊表,
    )
    assert 執行器.執行(發布執行請求({"captured": True})).text == "完成"
    assert len(模型.呼叫) == 1


@pytest.mark.parametrize("路徑", ["a\x00b", "a/./b", "a/../b", "a/" + "/".join(["b"] * 16), "\ud800.md"])
def test_技能套件拒絕控制字元深度dot與surrogate(路徑):
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        技能套件檔案(path=路徑, sha256="a" * 64, content=b"x")


def test_bundle拒絕失序重複與同長內容偽造():
    import hashlib
    甲 = 技能套件檔案(path="a.md", sha256=hashlib.sha256(b"a").hexdigest(), content=b"a")
    乙 = 技能套件檔案(path="b.md", sha256=hashlib.sha256(b"b").hexdigest(), content=b"b")
    for 檔案們 in ((乙, 甲), (甲, 甲)):
        with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
            技能套件快照(endpoint_version_id="ver-2", skill_bundle_hash="a" * 64,
                       manifest_digest="a" * 64, files=檔案們)
    object.__setattr__(甲, "content", b"z")
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        計算技能套件雜湊((甲, 乙))


def test_一次模型呼叫不執行工具且每次輸出輸入皆隔離():
    執行器, *_, 模型 = _建立測試執行器()
    請求 = 發布執行請求({"value": [1]})
    請求.input["value"].append(9)
    回應 = 執行器.執行(請求)
    assert 回應.text == "完成" and len(模型.呼叫) == 1
    傳入 = 模型.呼叫[0]["messages"][1]["metadata"]["input_json"]
    assert 傳入 == {"value": [1]}
