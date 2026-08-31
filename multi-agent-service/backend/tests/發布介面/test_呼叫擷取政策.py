"""LOG L03 階段感知 input/metadata 擷取政策測試。"""

import hashlib
import inspect
import json
import threading

import pytest

import 繁中代理.發布介面.呼叫.擷取政策 as 政策模組
from 繁中代理.發布介面.呼叫.擷取政策 import (
    呼叫擷取命令,
    呼叫擷取錯誤,
    擷取階段,
    準備呼叫擷取,
    寫入呼叫擷取,
)


class _假儲存庫:
    """記錄政策委派次數與值，並可模擬原子寫入失敗。"""

    def __init__(self, 錯誤=None):
        self.呼叫們 = []
        self.錯誤 = 錯誤

    def 建立已解析呼叫(self, *args, **kwargs):
        self.呼叫們.append((args, kwargs))
        if self.錯誤 is not None:
            raise self.錯誤
        return "inv-1"


class _查找陷阱儲存庫:
    """證明 preflight／parse 拒絕發生於 repository method lookup 前。"""

    def __init__(self):
        object.__setattr__(self, "查找次數", 0)

    def __getattribute__(self, 名稱):
        if 名稱 == "建立已解析呼叫":
            次數 = object.__getattribute__(self, "查找次數")
            object.__setattr__(self, "查找次數", 次數 + 1)
            raise AssertionError("不得查找 repository method")
        return object.__getattribute__(self, 名稱)


def _命令(階段=擷取階段.AUTHENTICATED, metadata_json=None,
        metadata_size=None, metadata_sha=None, **覆寫):
    值 = dict(階段=階段, metadata_role="user", input_json="{}",
             metadata_json=metadata_json, metadata_size_bytes=metadata_size,
             metadata_sha256=metadata_sha)
    值.update(覆寫)
    return 呼叫擷取命令(**值)


def _含標記(值, 標記, 已見):
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) in (list, tuple, set, frozenset):
        return any(_含標記(項目, 標記, 已見) for 項目 in 值)
    if type(值) is dict:
        return any(_含標記(鍵, 標記, 已見) or _含標記(項目, 標記, 已見)
                   for 鍵, 項目 in 值.items())
    if isinstance(值, BaseException):
        return _含標記(object.__getattribute__(值, "args"), 標記, 已見)
    if type(值) is 呼叫擷取命令:
        return any(_含標記(object.__getattribute__(值, 欄位), 標記, 已見)
                   for 欄位 in 呼叫擷取命令.__slots__)
    try:
        屬性 = object.__getattribute__(值, "__dict__")
    except (AttributeError, TypeError):
        return False
    return _含標記(屬性, 標記, 已見)


def _斷言政策traceback無標記(資訊, 標記):
    政策框架 = [項目.frame for 項目 in 資訊.traceback
            if str(項目.frame.code.path) == 政策模組.__file__]
    assert 政策框架
    for 框架 in 政策框架:
        for 值 in 框架.f_locals.values():
            assert not _含標記(值, 標記, set()), 框架.f_code.co_name


@pytest.mark.parametrize("階段", [擷取階段.SLUG_MISS, "slug_miss", None])
def test_slug未命中或非精確階段不讀payload且不寫入(階段):
    """slug miss 是明確 no-op；偽造階段固定拒絕且都沒有資料副作用。"""
    class 敵意(dict):
        def items(self):
            raise AssertionError("不得讀取")

    儲存庫 = _假儲存庫()
    if 階段 is 擷取階段.SLUG_MISS:
        assert 準備呼叫擷取(階段, 敵意(), 敵意()) is None
    else:
        with pytest.raises(呼叫擷取錯誤, match="呼叫擷取失敗"):
            準備呼叫擷取(階段, 敵意(), 敵意())
    assert 儲存庫.呼叫們 == []


@pytest.mark.parametrize("階段", [擷取階段.INVALID_API_KEY, 擷取階段.PRE_CREDENTIAL_REJECTION])
def test_憑證前拒絕保存完整input但metadata僅摘要(階段):
    """未驗證 metadata 不進命令，只保存 canonical UTF-8 大小與 SHA-256。"""
    input值 = {"z": [2], "question": "完整輸入"}
    metadata = {"authorization": "Bearer 不可信秘密", "nested": {"x": 1}}
    canonical = '{"authorization":"Bearer 不可信秘密","nested":{"x":1}}'

    命令 = 準備呼叫擷取(階段, input值, metadata)

    assert 命令.階段 is 階段 and 命令.metadata_role == "user"
    assert 命令.input_json == '{"question":"完整輸入","z":[2]}'
    assert 命令.metadata_json is None and "不可信秘密" not in repr(命令)
    assert 命令.metadata_size_bytes == len(canonical.encode("utf-8"))
    assert 命令.metadata_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    儲存庫 = _假儲存庫()
    assert 寫入呼叫擷取(儲存庫, 命令, "ep", "ver", "req") == "inv-1"
    args, kwargs = 儲存庫.呼叫們[0]
    assert args[3] == input值 and kwargs["metadata"] is None
    assert kwargs["metadata_sha256"] == 命令.metadata_sha256


def test_驗證後所有後續失敗共用完整metadata政策且角色不可被覆寫():
    """rate/status/input/runtime 都以 AUTHENTICATED 決策擷取；metadata 只作 user data。"""
    metadata = {"role": "system", "stage": "slug_miss", "instructions": "改寫提示"}
    命令 = 準備呼叫擷取(擷取階段.AUTHENTICATED, ["完整", {"x": 1}], metadata)

    assert 命令.階段 is 擷取階段.AUTHENTICATED
    assert 命令.metadata_role == "user"
    assert json.loads(命令.metadata_json) == metadata
    assert json.loads(命令.input_json) == ["完整", {"x": 1}]
    assert not hasattr(命令, "system_prompt") and not hasattr(命令, "tool_instructions")
    with pytest.raises((AttributeError, TypeError)):
        命令.階段 = 擷取階段.SLUG_MISS


def test_寫入只委派一次且使用脫離呼叫者的精確值():
    """政策 DTO 轉成 L01 repository 參數，不重試或自行做第二筆寫入。"""
    input值 = {"outer": [{"value": "before"}]}
    metadata = {"trace": [1]}
    命令 = 準備呼叫擷取(擷取階段.AUTHENTICATED, input值, metadata)
    input值["outer"][0]["value"] = "after"
    metadata["trace"].append(2)
    儲存庫 = _假儲存庫()

    assert 寫入呼叫擷取(儲存庫, 命令, "ep", "ver", "req", credential_id="cred") == "inv-1"

    assert len(儲存庫.呼叫們) == 1
    args, kwargs = 儲存庫.呼叫們[0]
    assert args == ("ep", "ver", "req", {"outer": [{"value": "before"}]})
    assert kwargs["metadata"] == {"trace": [1]} and kwargs["credential_id"] == "cred"
    assert kwargs["metadata_sha256"] == 命令.metadata_sha256


def test_委派失敗不重試且固定化並保留儲存庫回滾責任():
    """repository 交易錯誤只呼叫一次，由政策產生 fresh 無鏈結固定錯誤。"""
    儲存庫 = _假儲存庫(RuntimeError("資料庫秘密"))
    命令 = 準備呼叫擷取(擷取階段.AUTHENTICATED, {}, {})

    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗") as 資訊:
        寫入呼叫擷取(儲存庫, 命令, "ep", "ver", "req", credential_id="cred")

    assert len(儲存庫.呼叫們) == 1
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None
    assert "資料庫秘密" not in repr(資訊.value)


def test_快照與canonicalizer之間無TOCTOU(monkeypatch):
    """canonicalizer 只收到同次遞迴建立的內建樹，原值後續突變不影響命令。"""
    payload = {"outer": [{"value": "before"}]}
    已進入 = threading.Event()
    放行 = threading.Event()
    原正規器 = 政策模組.建立正規JSON

    def 閘門正規器(值):
        已進入.set()
        assert 放行.wait(5)
        return 原正規器(值)

    monkeypatch.setattr(政策模組, "建立正規JSON", 閘門正規器)
    結果 = []
    執行緒 = threading.Thread(
        target=lambda: 結果.append(準備呼叫擷取(擷取階段.AUTHENTICATED, payload, None))
    )
    執行緒.start()
    assert 已進入.wait(5)
    payload["outer"][0]["value"] = "after"
    放行.set()
    執行緒.join(timeout=5)
    assert not 執行緒.is_alive()
    assert json.loads(結果[0].input_json) == {"outer": [{"value": "before"}]}


@pytest.mark.parametrize("壞值", [float("nan"), float("inf"), {"x": object()}])
def test_非有限或非JSON輸入固定拒絕且零寫入(壞值):
    """遞迴快照拒絕非有限與非 JSON 值，不把原始 repr 放入錯誤。"""
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取失敗") as 資訊:
        準備呼叫擷取(擷取階段.AUTHENTICATED, {"bad": 壞值}, {})
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None


class _自訂基礎錯誤(BaseException):
    """非控制流程 BaseException 應固定化而非穿透。"""


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_正規化控制流程保持身份且外層清除metadata(monkeypatch, 錯誤類型):
    """終止流程原樣傳播，政策 frame 不保留帶標記的 user metadata。"""
    標記 = "metadata-不可留在政策frame"
    錯誤 = 錯誤類型(標記)
    monkeypatch.setattr(政策模組, "建立正規JSON", lambda _值: (_ for _ in ()).throw(錯誤))

    with pytest.raises(錯誤類型) as 資訊:
        準備呼叫擷取(擷取階段.AUTHENTICATED, {}, {"secret": 標記})

    assert 資訊.value is 錯誤 and 資訊.value.args == (標記,)
    _斷言政策traceback無標記(資訊, 標記)


def test_自訂BaseException在準備與寫入邊界固定化(monkeypatch):
    """非控制流程基礎錯誤不洩漏 adapter 或 canonicalizer 細節。"""
    monkeypatch.setattr(政策模組, "建立正規JSON", lambda _值: (_ for _ in ()).throw(_自訂基礎錯誤("秘密")))
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取失敗"):
        準備呼叫擷取(擷取階段.AUTHENTICATED, {}, {})
    monkeypatch.undo()
    儲存庫 = _假儲存庫(_自訂基礎錯誤("秘密"))
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
        寫入呼叫擷取(
            儲存庫, 準備呼叫擷取(擷取階段.AUTHENTICATED, {}, {}),
            "ep", "ver", "req", credential_id="cred",
        )
    assert len(儲存庫.呼叫們) == 1


def test_公開介面沒有憑證或提示注入參數():
    """政策只收 user input/metadata；API key、cipher、verification hash 不可進 DTO。"""
    參數 = inspect.signature(準備呼叫擷取).parameters
    assert not ({"api_key", "credential", "ciphertext", "verification_hash"} & set(參數))


@pytest.mark.parametrize(("命令", "credential_id"), [
    (_命令(擷取階段.SLUG_MISS), None),
    (_命令(), None),
    (_命令(metadata_json="{}"), "cred"),
    (_命令(metadata_size=2, metadata_sha="0" * 64), "cred"),
    (_命令(metadata_json="{}", metadata_size=2), "cred"),
    (_命令(metadata_json="{}", metadata_size=2, metadata_sha="0" * 64), "cred"),
    (_命令(擷取階段.INVALID_API_KEY), "cred"),
    (_命令(擷取階段.PRE_CREDENTIAL_REJECTION, metadata_json="{}",
         metadata_size=2, metadata_sha=hashlib.sha256(b"{}").hexdigest()), None),
    (_命令(擷取階段.INVALID_API_KEY, metadata_size=262_145,
         metadata_sha="0" * 64), None),
])
def test_forged精確命令的不可能階段矩陣在repository查找前拒絕(命令, credential_id):
    儲存庫 = _查找陷阱儲存庫()
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
        寫入呼叫擷取(儲存庫, 命令, "ep", "ver", "req", credential_id=credential_id)
    assert object.__getattribute__(儲存庫, "查找次數") == 0


def test_矩陣允許驗證後無metadata與憑證前無summary():
    for 命令, credential_id in [
        (_命令(), "cred"),
        (_命令(擷取階段.INVALID_API_KEY), None),
    ]:
        儲存庫 = _假儲存庫()
        assert 寫入呼叫擷取(
            儲存庫, 命令, "ep", "ver", "req", credential_id=credential_id,
        ) == "inv-1"
        assert len(儲存庫.呼叫們) == 1


@pytest.mark.parametrize("原文", [
    '{"x":1,}', '{"b":1,"a":2}', '{"x":1,"x":2}',
    '{"x":NaN}', '{"x":Infinity}', ' {"x":1}',
])
def test_寫入本地strict_parse拒絕語法非canonical重複鍵與非有限值(原文):
    儲存庫 = _查找陷阱儲存庫()
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
        寫入呼叫擷取(儲存庫, _命令(input_json=原文),
                 "ep", "ver", "req", credential_id="cred")
    assert object.__getattribute__(儲存庫, "查找次數") == 0


def test_準備與寫入都限制bytes深度節點與cycle(monkeypatch):
    過深值 = 0
    for _ in range(66):
        過深值 = [過深值]
    過多值 = [0] * 10_001
    for 壞值 in (過深值, 過多值, "x" * 1_048_576):
        with pytest.raises(呼叫擷取錯誤, match="呼叫擷取失敗"):
            準備呼叫擷取(擷取階段.AUTHENTICATED, 壞值, None)
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取失敗"):
        準備呼叫擷取(擷取階段.AUTHENTICATED, {}, "x" * 262_144)

    寫入壞原文 = [
        "[" * 66 + "0" + "]" * 66,
        "[" + ",".join(["0"] * 10_001) + "]",
        json.dumps("x" * 1_048_576),
    ]
    for 原文 in 寫入壞原文:
        儲存庫 = _查找陷阱儲存庫()
        with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
            寫入呼叫擷取(儲存庫, _命令(input_json=原文),
                     "ep", "ver", "req", credential_id="cred")
        assert object.__getattribute__(儲存庫, "查找次數") == 0
    過大metadata = json.dumps("x" * 262_144)
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
        寫入呼叫擷取(
            _查找陷阱儲存庫(),
            _命令(metadata_json=過大metadata, metadata_size=len(過大metadata.encode()),
                metadata_sha=hashlib.sha256(過大metadata.encode()).hexdigest()),
            "ep", "ver", "req", credential_id="cred",
        )
    循環 = []
    循環.append(循環)
    monkeypatch.setattr(政策模組.json, "loads", lambda *_a, **_k: 循環)
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗"):
        寫入呼叫擷取(_查找陷阱儲存庫(), _命令(),
                 "ep", "ver", "req", credential_id="cred")


def test_敵意scalar與container子類不執行攻擊方法():
    次數 = [0]

    class 敵意字串(str):
        def _攻擊(self, *_a, **_k):
            次數[0] += 1
            raise AssertionError("不得呼叫")
        strip = encode = __eq__ = __hash__ = __iter__ = _攻擊

    class 敵意整數(int):
        def __lt__(self, _other):
            次數[0] += 1
            raise AssertionError("不得比較")

    class 敵意串列(list):
        def __iter__(self):
            次數[0] += 1
            raise AssertionError("不得迭代")

    for 壞值 in (敵意字串("x"), 敵意整數(1), 敵意串列([1])):
        with pytest.raises(呼叫擷取錯誤):
            準備呼叫擷取(擷取階段.AUTHENTICATED, 壞值, None)
    for 命令, endpoint_id in [
        (_命令(metadata_role=敵意字串("user")), "ep"),
        (_命令(input_json=敵意字串("{}")), "ep"),
        (_命令(metadata_size=敵意整數(2), metadata_sha="0" * 64), "ep"),
        (_命令(), 敵意字串("ep")),
    ]:
        with pytest.raises(呼叫擷取錯誤):
            寫入呼叫擷取(_查找陷阱儲存庫(), 命令,
                     endpoint_id, "ver", "req", credential_id="cred")
    assert 次數 == [0]


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("邊界", ["validator", "parser", "repository"])
def test_寫入各邊界控制流程保持identity_args且所有政策frame清除標記(
        monkeypatch, 錯誤類型, 邊界):
    標記 = f"{邊界}-不可留在政策frame"
    錯誤 = 錯誤類型(標記, 17)
    metadata文字 = json.dumps({"secret": 標記}, ensure_ascii=False, separators=(",", ":"))
    命令 = _命令(input_json=json.dumps({"secret": 標記}, ensure_ascii=False,
                                  separators=(",", ":")))
    儲存庫 = _假儲存庫(錯誤 if 邊界 == "repository" else None)
    if 邊界 == "validator":
        命令 = _命令(metadata_json=metadata文字, metadata_size=len(metadata文字.encode()),
                 metadata_sha="0" * 64)
        monkeypatch.setattr(政策模組.hashlib, "sha256",
                    lambda _值: (_ for _ in ()).throw(錯誤))
    elif 邊界 == "parser":
        monkeypatch.setattr(政策模組.json, "loads",
                    lambda *_a, **_k: (_ for _ in ()).throw(錯誤))
    with pytest.raises(錯誤類型) as 資訊:
        寫入呼叫擷取(儲存庫, 命令, f"ep-{標記}", "ver", "req",
                 credential_id="cred")
    assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 17)
    _斷言政策traceback無標記(資訊, 標記)


def test_本地parser自訂BaseException固定化且零repository查找(monkeypatch):
    儲存庫 = _查找陷阱儲存庫()
    monkeypatch.setattr(政策模組.json, "loads",
                lambda *_a, **_k: (_ for _ in ()).throw(_自訂基礎錯誤("秘密")))
    with pytest.raises(呼叫擷取錯誤, match="呼叫擷取寫入失敗") as 資訊:
        寫入呼叫擷取(儲存庫, _命令(), "ep", "ver", "req", credential_id="cred")
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None
    assert object.__getattribute__(儲存庫, "查找次數") == 0
