"""發布執行期 revision-pinned 工具快照契約。"""

import hashlib
import json
import threading
import types

import pytest

from 繁中代理.工具 import 工具定義
import 繁中代理.發布介面.執行期.工具版本庫 as 版本庫模組
import 繁中代理.工具註冊 as 工具註冊模組
from 繁中代理.發布介面.執行期.工具版本庫 import (
    工具快照錯誤,
    工具快照項目,
    工具版本庫,
    建立版本釘選工具登錄器,
)


def _工具(名稱, 回傳, 說明="說明"):
    return 工具定義(名稱, 說明, {"type": "object", "properties": {}}, lambda 參數: 回傳)


def _assert_發布框架無標記(traceback, marker):
    def 含標記(值, 已看):
        if id(值) in 已看:
            return False
        已看.add(id(值))
        if type(值) is str:
            return marker in 值
        if type(值) is dict:
            return any(含標記(鍵, 已看) or 含標記(子值, 已看) for 鍵, 子值 in 值.items())
        if type(值) in (tuple, list, set, frozenset):
            return any(含標記(子值, 已看) for 子值 in 值)
        if isinstance(值, BaseException):
            return 含標記(值.args, 已看)
        if hasattr(值, "__dict__") and 含標記(vars(值), 已看):
            return True
        for 類別 in type(值).__mro__:
            欄位們 = 類別.__dict__.get("__slots__", ())
            if type(欄位們) is str:
                欄位們 = (欄位們,)
            for 欄位 in 欄位們:
                try:
                    if 含標記(object.__getattribute__(值, 欄位), 已看):
                        return True
                except (AttributeError, TypeError):
                    pass
        return False

    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__", "").startswith("繁中代理"):
            assert not any(含標記(值, set()) for 值 in frame.f_locals.values()), frame.f_code.co_name
        traceback = traceback.tb_next


def test_工具版本庫產生小寫SHA256且拒絕重複修訂():
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", _工具("lookup", "v1"))
    assert type(項目) is 工具快照項目
    assert 項目.digest == 項目.digest.lower()
    assert len(項目.digest) == 64
    int(項目.digest, 16)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        庫.登錄修訂("rev-1", _工具("lookup", "取代"))


def test_移除修訂後永久拒絕重用identity且舊快照失敗關閉():
    庫 = 工具版本庫()
    處理甲 = _工具("lookup", "甲")
    快照 = 庫.登錄修訂("rev-1", 處理甲)
    已釘選 = 建立版本釘選工具登錄器(庫, (快照,))
    庫.移除修訂("lookup", "rev-1")

    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        庫.登錄修訂("rev-1", _工具("lookup", "乙"))
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(庫, (快照,))
    assert json.loads(已釘選.呼叫工具("lookup", {}))["result"] == "甲"


def test_登錄驗證失敗不占用identity():
    庫 = 工具版本庫()
    無效工具 = _工具("lookup", "bad")
    object.__setattr__(無效工具, "處理函數", None)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        庫.登錄修訂("rev-1", 無效工具)
    assert 庫.登錄修訂("rev-1", _工具("lookup", "ok")).name == "lookup"


def test_建立修訂早期拒絕會遞迴清理所有caller_owned欄位():
    marker = "EARLY/REVISION/SECRET"

    class 處理器:
        def __init__(self):
            self.marker = marker

        def __call__(self, 參數):
            return self.marker

    工具 = 工具定義(
        marker, marker, {"type": "object", "marker": marker}, 處理器()
    )
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        工具版本庫().登錄修訂(marker, 工具)
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, marker)


@pytest.mark.parametrize("已移除", [False, True], ids=["duplicate", "tombstone"])
def test_重複或永久tombstone拒絕在鎖外清理authoritative_state且狀態不變(已移除):
    marker = "DUPLICATE_REVISION_SECRET"
    庫 = 工具版本庫()
    原工具 = 工具定義(
        marker, marker, {"type": "object", "marker": marker}, lambda 參數: marker
    )
    快照 = 庫.登錄修訂(marker, 原工具)
    已釘選 = 建立版本釘選工具登錄器(庫, (快照,))
    if 已移除:
        庫.移除修訂(marker, marker)
    原修訂 = dict(庫._修訂)
    原墓碑 = set(庫._已使用識別)

    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        庫.登錄修訂(marker, 原工具)
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, marker)
    assert 庫._修訂 == 原修訂
    assert 庫._已使用識別 == 原墓碑
    if 已移除:
        assert 庫.取得工具修訂(marker, marker) is None
    else:
        assert 庫.取得工具修訂(marker, marker).摘要 == 快照.digest
    assert json.loads(已釘選.呼叫工具(marker, {}))["result"] == marker


def test_provider_getter回傳脫離副本且竄改不影響後續解析():
    處理甲 = lambda 參數: "甲"
    處理乙 = lambda 參數: "乙"
    庫 = 工具版本庫()
    快照 = 庫.登錄修訂("rev-1", 工具定義("lookup", "原說明", {}, 處理甲))
    原摘要 = 快照.digest

    第一 = 庫.取得工具修訂("lookup", "rev-1")
    assert type(第一) is 版本庫模組._工具修訂
    object.__setattr__(第一, "名稱", "evil")
    object.__setattr__(第一, "修訂名稱", "evil-rev")
    object.__setattr__(第一, "摘要", "0" * 64)
    object.__setattr__(第一, "說明", "竄改")
    object.__setattr__(第一, "參數JSON", '{"evil":true}')
    object.__setattr__(第一, "處理函數", 處理乙)

    第二 = 庫.取得工具修訂("lookup", "rev-1")
    assert type(第二) is 版本庫模組._工具修訂
    assert 第一 is not 第二
    assert (第二.名稱, 第二.修訂名稱, 第二.摘要, 第二.說明, 第二.參數JSON) == (
        "lookup", "rev-1", 原摘要, "原說明", "{}"
    )
    assert 第二.處理函數 is not 處理甲
    assert 快照.digest == 原摘要
    登錄器 = 建立版本釘選工具登錄器(庫, (快照,))
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "甲"


def test_有狀態callable在登錄_provider與釘選三層皆隔離():
    class 處理器:
        def __init__(self, 結果):
            self.結果 = 結果

        def __call__(self, 參數):
            return self.結果

    原始 = 處理器("甲")
    庫 = 工具版本庫()
    快照 = 庫.登錄修訂("rev-1", 工具定義("lookup", "說明", {}, 原始))
    原始.結果 = "乙"
    provider副本 = 庫.取得工具修訂("lookup", "rev-1")
    provider副本.處理函數.結果 = "丙"
    登錄器 = 建立版本釘選工具登錄器(庫, (快照,))
    後續副本 = 庫.取得工具修訂("lookup", "rev-1")

    assert 後續副本.處理函數.結果 == "甲"
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "甲"


def test_exact函數程式_defaults_attrs與provider副本皆隔離():
    def 原始(參數, 狀態={"結果": "甲"}):
        return 狀態["結果"]

    def 竄改(參數):
        return "乙"

    原始.標記 = {"值": "原始"}
    庫 = 工具版本庫()
    快照 = 庫.登錄修訂("rev-1", 工具定義("lookup", "說明", {}, 原始))
    原始.__code__ = 竄改.__code__
    原始.__defaults__[0]["結果"] = "乙"
    原始.標記["值"] = "竄改"
    provider副本 = 庫.取得工具修訂("lookup", "rev-1")
    assert type(provider副本.處理函數) is types.FunctionType
    assert provider副本.處理函數.標記 == {"值": "原始"}
    provider副本.處理函數.__code__ = 竄改.__code__
    provider副本.處理函數.__defaults__[0]["結果"] = "丙"

    登錄器 = 建立版本釘選工具登錄器(庫, (快照,))
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "甲"


def test_exact函數在deepcopy前一次捕捉所有binding():
    class 竄改原函數:
        def __deepcopy__(self, memo):
            原始.__code__ = 竄改.__code__
            原始.__defaults__ = (object(),)
            原始.__dict__.clear()
            return object()

    def 原始(參數, 觸發器=竄改原函數()):
        return "甲"

    def 竄改(參數, 觸發器=None):
        return "乙"

    原始.標記 = "原始"
    庫 = 工具版本庫()
    快照 = 庫.登錄修訂("rev-1", 工具定義("lookup", "原說明", {}, 原始))
    登錄器 = 建立版本釘選工具登錄器(庫, (快照,))

    assert 原始({}) == "乙"
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "甲"
    assert 庫.取得工具修訂("lookup", "rev-1").處理函數.標記 == "原始"


def test_callable_deepcopy竄改外層工具仍只使用一次捕捉欄位():
    class 處理器:
        def __init__(self, 結果, 外層=None):
            self.結果 = 結果
            self.外層 = 外層

        def __call__(self, 參數):
            return self.結果

        def __deepcopy__(self, memo):
            if self.外層 is not None:
                object.__setattr__(self.外層, "名稱", "evil")
                object.__setattr__(self.外層, "說明", "竄改說明")
                object.__setattr__(self.外層, "參數結構", {"evil": True})
                object.__setattr__(self.外層, "處理函數", 處理器("乙"))
            return 處理器(self.結果)

    原處理器 = 處理器("甲")
    工具 = 工具定義("lookup", "原說明", {"type": "object"}, 原處理器)
    原處理器.外層 = 工具
    庫 = 工具版本庫()
    快照 = 庫.登錄修訂("rev-1", 工具)
    修訂 = 庫.取得工具修訂("lookup", "rev-1")
    登錄器 = 建立版本釘選工具登錄器(庫, (快照,))

    assert (快照.name, 修訂.名稱, 修訂.說明, 修訂.參數JSON) == (
        "lookup", "lookup", "原說明", '{"type":"object"}'
    )
    assert 庫.取得工具修訂("evil", "rev-1") is None
    assert 快照.digest == hashlib.sha256(
        b'{"description":"\xe5\x8e\x9f\xe8\xaa\xaa\xe6\x98\x8e","name":"lookup","parameters":{"type":"object"},"revision":"rev-1"}'
    ).hexdigest()
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "甲"


def test_不安全deepcopy_callable固定拒絕且不占用identity():
    class 不安全:
        def __call__(self, 參數):
            return "bad"

        def __deepcopy__(self, memo):
            return self

    庫 = 工具版本庫()
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        庫.登錄修訂("rev-1", 工具定義("lookup", "說明", {}, 不安全()))
    assert 庫.登錄修訂("rev-1", _工具("lookup", "ok")).name == "lookup"


def test_deepcopy回傳非callable固定拒絕():
    class 不安全:
        def __call__(self, 參數):
            return "bad"

        def __deepcopy__(self, memo):
            return object()

    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        工具版本庫().登錄修訂("rev-1", 工具定義("lookup", "說明", {}, 不安全()))


def test_callable_deepcopy自訂BaseException固定失敗並清理locals():
    marker = "HANDLER_COPY_SECRET"

    class 惡意錯誤(BaseException):
        pass

    class 不安全:
        def __call__(self, 參數):
            return marker

        def __deepcopy__(self, memo):
            raise 惡意錯誤(marker)

    庫 = 工具版本庫()
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        庫.登錄修訂("rev-1", 工具定義(marker, marker, {}, 不安全()))
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_callable_deepcopy控制流程原樣通過並清理locals(例外型別):
    marker = "HANDLER_COPY_SECRET"
    中斷 = 例外型別(marker)

    class 不安全:
        def __call__(self, 參數):
            return marker

        def __deepcopy__(self, memo):
            raise 中斷

    with pytest.raises(例外型別) as 錯誤:
        工具版本庫().登錄修訂("rev-1", 工具定義(marker, marker, {}, 不安全()))
    assert 錯誤.value is 中斷
    _assert_發布框架無標記(錯誤.tb, marker)


def test_provider_getter重建自訂BaseException固定失敗且清理locals(monkeypatch):
    marker = "STORED_REVISION_SECRET"

    class 惡意錯誤(BaseException):
        pass

    庫 = 工具版本庫()
    庫.登錄修訂("rev-1", _工具(marker, "甲", marker))

    def 重建失敗(欄位):
        raise 惡意錯誤(marker)

    monkeypatch.setattr(版本庫模組, "_重建已存修訂", 重建失敗)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        庫.取得工具修訂(marker, "rev-1")
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_provider_getter重建控制流程原樣通過且清理locals(monkeypatch, 例外型別):
    marker = "STORED_REVISION_SECRET"
    中斷 = 例外型別(marker)
    庫 = 工具版本庫()
    庫.登錄修訂("rev-1", _工具(marker, "甲", marker))

    def 重建中斷(欄位):
        raise 中斷

    monkeypatch.setattr(版本庫模組, "_重建已存修訂", 重建中斷)
    with pytest.raises(例外型別) as 錯誤:
        庫.取得工具修訂(marker, "rev-1")
    assert 錯誤.value is 中斷
    assert 錯誤.value.args == (marker,)
    _assert_發布框架無標記(錯誤.tb, marker)


def test_並行登錄同一identity僅一個成功且移除後仍不可重用():
    庫 = 工具版本庫()
    起跑 = threading.Barrier(3)
    結果 = []

    def 登錄(回傳):
        起跑.wait()
        try:
            結果.append(("成功", 庫.登錄修訂("rev-1", _工具("lookup", 回傳))))
        except 工具快照錯誤:
            結果.append(("拒絕", None))

    執行緒們 = [threading.Thread(target=登錄, args=(值,)) for 值 in ("甲", "乙")]
    for 執行緒 in 執行緒們:
        執行緒.start()
    起跑.wait()
    for 執行緒 in 執行緒們:
        執行緒.join(2)
    assert all(not 執行緒.is_alive() for 執行緒 in 執行緒們)
    assert sorted(狀態 for 狀態, _ in 結果) == ["成功", "拒絕"]

    庫.移除修訂("lookup", "rev-1")
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        庫.登錄修訂("rev-1", _工具("lookup", "丙"))


def test_快照只解析exact名稱修訂與digest且不受live_mutation影響():
    庫 = 工具版本庫()
    v1 = 庫.登錄修訂("rev-1", _工具("lookup", "v1"))
    已釘選 = 建立版本釘選工具登錄器(庫, (v1,))
    庫.登錄修訂("rev-2", _工具("lookup", "v2"))
    庫.移除修訂("lookup", "rev-1")

    assert json.loads(已釘選.呼叫工具("lookup", {}))["result"] == "v1"
    assert [項["function"]["name"] for 項 in 已釘選.列出工具結構()] == ["lookup"]
    assert json.loads(已釘選.呼叫工具("unknown", {})) == {
        "success": False, "error": "發布工具不可用"
    }


@pytest.mark.parametrize("模式", ["unknown", "missing_revision", "digest_mismatch"])
def test_未知工具缺少修訂與digest不符皆失敗關閉(模式):
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", _工具("lookup", "v1"))
    if 模式 == "unknown":
        項目 = 工具快照項目(name="other", revision="rev-1", digest=項目.digest)
    elif 模式 == "missing_revision":
        項目 = 工具快照項目(name="lookup", revision="rev-2", digest=項目.digest)
    else:
        項目 = 工具快照項目(name="lookup", revision="rev-1", digest="0" * 64)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        建立版本釘選工具登錄器(庫, (項目,))
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_重複工具與偽造DTO在provider_lookup前拒絕():
    項目 = 工具快照項目(name="lookup", revision="rev-1", digest="a" * 64)

    class 提供者:
        def __init__(self):
            self.呼叫數 = 0

        def 取得工具修訂(self, name, revision):
            self.呼叫數 += 1
            raise AssertionError("不可呼叫")

    提供者物件 = 提供者()
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者物件, (項目, 項目))
    assert 提供者物件.呼叫數 == 0

    class 偽造:
        @property
        def name(self):
            raise AssertionError("不可讀取")

    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者物件, (偽造(),))
    assert 提供者物件.呼叫數 == 0


@pytest.mark.parametrize("欄位", ["name", "revision", "digest"])
def test_被竄改exact_DTO的非exact欄位不觸發hash_eq或provider(欄位):
    計數 = {"hash": 0, "eq": 0}

    class 惡意字串(str):
        def __hash__(self):
            計數["hash"] += 1
            raise AssertionError("不可雜湊")

        def __eq__(self, other):
            計數["eq"] += 1
            raise AssertionError("不可比較")

    class 提供者:
        呼叫數 = 0

        def 取得工具修訂(self, name, revision):
            self.呼叫數 += 1
            raise AssertionError("不可呼叫")

    項目 = 工具快照項目(name="lookup", revision="rev-1", digest="a" * 64)
    object.__setattr__(項目, 欄位, 惡意字串(object.__getattribute__(項目, 欄位)))
    提供者物件 = 提供者()
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者物件, (項目,))
    assert 提供者物件.呼叫數 == 0
    assert 計數 == {"hash": 0, "eq": 0}


def test_完整預檢並重建快照DTO後才開始provider_lookup():
    第一 = 工具快照項目(name="first", revision="rev-1", digest="a" * 64)
    第二 = 工具快照項目(name="second", revision="rev-1", digest="b" * 64)
    object.__setattr__(第二, "revision", "")

    class 提供者:
        呼叫數 = 0

        def 取得工具修訂(self, name, revision):
            self.呼叫數 += 1
            raise AssertionError("不可呼叫")

    提供者物件 = 提供者()
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者物件, (第一, 第二))
    assert 提供者物件.呼叫數 == 0

    庫 = 工具版本庫()
    原始 = 庫.登錄修訂("rev-1", _工具("lookup", "ok"))

    class 竄改來源:
        def 取得工具修訂(self, name, revision):
            object.__setattr__(原始, "name", "tampered")
            return 庫.取得工具修訂(name, revision)

    登錄器 = 建立版本釘選工具登錄器(竄改來源(), (原始,))
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "ok"


def test_較後偽造DTO拒絕前不執行重複檢查或provider(monkeypatch):
    第一 = 工具快照項目(name="first", revision="rev-1", digest="a" * 64)
    第二 = 工具快照項目(name="second", revision="rev-1", digest="b" * 64)
    object.__setattr__(第二, "digest", object())
    計數 = {"duplicate": 0, "provider": 0}

    def 重複檢查(項目們):
        計數["duplicate"] += 1
        raise AssertionError("預檢完成前不可雜湊名稱")

    class 提供者:
        def 取得工具修訂(self, name, revision):
            計數["provider"] += 1
            raise AssertionError("預檢完成前不可查詢")

    monkeypatch.setattr(版本庫模組, "_有重複工具名稱", 重複檢查)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者(), (第一, 第二))
    assert 計數 == {"duplicate": 0, "provider": 0}


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_快照預檢控制流程例外清理後原樣通過(monkeypatch, 例外型別):
    marker = "OWNER_SECRET"
    項目 = 工具快照項目(name=marker, revision="rev-1", digest="a" * 64)

    def 中斷(value):
        raise 例外型別(marker)

    monkeypatch.setattr(版本庫模組, "_是識別碼", 中斷)
    with pytest.raises(例外型別) as 錯誤:
        建立版本釘選工具登錄器(object(), (項目,))
    assert 錯誤.value.args == (marker,)
    _assert_發布框架無標記(錯誤.tb, marker)


def test_快照預檢自訂BaseException轉固定錯誤並清理locals(monkeypatch):
    marker = "OWNER_SECRET"

    class 惡意錯誤(BaseException):
        pass

    項目 = 工具快照項目(name=marker, revision="rev-1", digest="a" * 64)

    def 失敗(value):
        raise 惡意錯誤(marker)

    monkeypatch.setattr(版本庫模組, "_是識別碼", 失敗)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        建立版本釘選工具登錄器(object(), (項目,))
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, marker)


def test_legacy預設版本庫在任何路徑使用者或登錄器存取前固定失敗(monkeypatch):
    計數 = {"registry": 0, "path": 0, "context": 0}

    def 禁止登錄器(*args, **kwargs):
        計數["registry"] += 1
        raise AssertionError("不可建立legacy registry")

    def 禁止路徑(*args, **kwargs):
        計數["path"] += 1
        raise AssertionError("不可讀取路徑")

    def 禁止上下文(*args, **kwargs):
        計數["context"] += 1
        raise AssertionError("不可讀取使用者上下文")

    monkeypatch.setattr(工具註冊模組, "建立預設工具登錄器", 禁止登錄器)
    monkeypatch.setattr(工具註冊模組, "Path", 禁止路徑)
    monkeypatch.setattr(工具註冊模組, "使用者可用工具", 禁止上下文)
    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$") as 錯誤:
        工具註冊模組.建立預設工具版本庫("OWNER_SECRET")
    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None
    _assert_發布框架無標記(錯誤.tb, "OWNER_SECRET")
    assert 計數 == {"registry": 0, "path": 0, "context": 0}


def test_工具schema與呼叫參數皆從呼叫端資料脫離且無owner注入():
    參數結構 = {"type": "object", "properties": {"q": {"type": "string"}}}
    收到 = []
    工具 = 工具定義("lookup", "說明", 參數結構, lambda 參數: 收到.append(參數) or "ok")
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", 工具)
    已釘選 = 建立版本釘選工具登錄器(庫, (項目,))
    參數結構["properties"]["owner_secret"] = {"type": "string"}
    結構 = 已釘選.列出工具結構()
    結構[0]["function"]["parameters"]["properties"]["tamper"] = {}

    呼叫參數 = {"q": "x"}
    assert json.loads(已釘選.呼叫工具("lookup", 呼叫參數))["success"] is True
    呼叫參數["later"] = True
    assert 收到 == [{"q": "x"}]
    assert not any(鍵.startswith("_current_") or "owner" in 鍵 for 鍵 in 收到[0])
    assert "owner_secret" not in 已釘選.列出工具結構()[0]["function"]["parameters"]["properties"]
    assert "tamper" not in 已釘選.列出工具結構()[0]["function"]["parameters"]["properties"]


def test_公開digest是正規內容而非大寫或呼叫物件身分():
    庫 = 工具版本庫()
    第一 = 庫.登錄修訂("rev-1", _工具("lookup", 1))
    第二庫 = 工具版本庫()
    第二 = 第二庫.登錄修訂("rev-1", _工具("lookup", 2))
    assert 第一.digest == 第二.digest
    assert 第一.digest == hashlib.sha256(
        b'{"description":"\xe8\xaa\xaa\xe6\x98\x8e","name":"lookup","parameters":{"properties":{},"type":"object"},"revision":"rev-1"}'
    ).hexdigest()


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_provider控制流程例外原樣通過並遞迴清理locals(例外型別):
    marker = "PROVIDER_OWNER_SECRET"
    項目 = 工具快照項目(name=marker, revision="rev-1", digest="a" * 64)
    中斷 = 例外型別(marker)

    class 提供者:
        def __init__(self):
            self.marker = marker

        def 取得工具修訂(self, name, revision):
            raise 中斷

    with pytest.raises(例外型別) as 錯誤:
        建立版本釘選工具登錄器(提供者(), (項目,))
    assert 錯誤.value is 中斷
    assert 錯誤.value.args == (marker,)
    _assert_發布框架無標記(錯誤.tb, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_provider結果正規化控制流程清理目前結果與輔助框架(monkeypatch, 例外型別):
    marker = "PROVIDER_RESULT_SECRET"
    中斷 = 例外型別(marker)
    項目 = 工具快照項目(name=marker, revision="rev-1", digest="a" * 64)
    修訂 = 版本庫模組._工具修訂(marker, "rev-1", "a" * 64, marker, "{}", lambda _: None)

    class 提供者:
        def 取得工具修訂(self, name, revision):
            return 修訂

    def 中斷驗證(欄位):
        raise 中斷

    monkeypatch.setattr(版本庫模組, "_修訂欄位合法", 中斷驗證)
    with pytest.raises(例外型別) as 錯誤:
        建立版本釘選工具登錄器(提供者(), (項目,))
    assert 錯誤.value is 中斷
    assert 錯誤.value.args == (marker,)
    _assert_發布框架無標記(錯誤.tb, marker)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_handler控制流程例外原樣通過並清空登錄器與參數locals(例外型別):
    marker = "HANDLER_OWNER_SECRET"
    中斷 = 例外型別(marker)

    def handler(arguments):
        raise 中斷

    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", 工具定義(marker, marker, {}, handler))
    登錄器 = 建立版本釘選工具登錄器(庫, (項目,))
    with pytest.raises(例外型別) as 錯誤:
        登錄器.呼叫工具(marker, {"secret": marker})
    assert 錯誤.value is 中斷
    assert 錯誤.value.args == (marker,)
    _assert_發布框架無標記(錯誤.tb, marker)


def test_provider自訂BaseException固定清理且保留欄位不可注入():
    class 惡意錯誤(BaseException):
        pass

    class 提供者:
        def 取得工具修訂(self, name, revision):
            raise 惡意錯誤("OWNER_SECRET")

    項目 = 工具快照項目(name="lookup", revision="rev-1", digest="a" * 64)
    with pytest.raises(工具快照錯誤) as 錯誤:
        建立版本釘選工具登錄器(提供者(), (項目,))
    assert str(錯誤.value) == "發布工具快照不可用"
    assert 錯誤.value.__context__ is None

    庫 = 工具版本庫()
    正常 = 庫.登錄修訂("rev-1", _工具("lookup", "ok"))
    登錄器 = 建立版本釘選工具登錄器(庫, (正常,))
    assert json.loads(登錄器.呼叫工具("lookup", {"_current_user_id": "owner"})) == {
        "success": False, "error": "發布工具不可用"
    }


def test_provider無法以匹配快照digest偽造不同schema():
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", _工具("lookup", "ok"))

    class 提供者:
        def 取得工具修訂(self, name, revision):
            return 版本庫模組._工具修訂(
                name, revision, 項目.digest, "竄改",
                '{"properties":{"owner":{"type":"string"}},"type":"object"}', lambda 參數: "bad"
            )

    with pytest.raises(工具快照錯誤, match="^發布工具快照不可用$"):
        建立版本釘選工具登錄器(提供者(), (項目,))


def test_呼叫參數快照期間插入保留鍵固定失敗且不呼叫handler(monkeypatch):
    """並行插入保留鍵不可穿越單次快照走訪。"""
    已進入 = threading.Event()
    可繼續 = threading.Event()
    呼叫參數 = {"q": "safe"}
    收到 = []
    原始取得 = 版本庫模組._取得字典項目

    def 暫停取得(字典):
        項目們 = 原始取得(字典)
        if 字典 is 呼叫參數:
            已進入.set()
            assert 可繼續.wait(2)
        return 項目們

    庫 = 工具版本庫()
    項目 = 庫.登錄修訂(
        "rev-1", 工具定義("lookup", "說明", {}, lambda 參數: 收到.append(參數) or "bad")
    )
    登錄器 = 建立版本釘選工具登錄器(庫, (項目,))
    monkeypatch.setattr(版本庫模組, "_取得字典項目", 暫停取得)
    結果 = []
    執行緒 = threading.Thread(target=lambda: 結果.append(登錄器.呼叫工具("lookup", 呼叫參數)))
    執行緒.start()
    assert 已進入.wait(2)
    呼叫參數["_current_user_id"] = "attacker"
    可繼續.set()
    執行緒.join(2)

    assert not 執行緒.is_alive()
    assert 收到 == []
    assert json.loads(結果[0]) == {"success": False, "error": "發布工具不可用"}


@pytest.mark.parametrize("不合法值", [float("nan"), float("inf")])
def test_JSON快照拒絕非有限數且不呼叫handler(不合法值):
    """非有限 JSON 數值固定失敗關閉。"""
    收到 = []
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂(
        "rev-1", 工具定義("lookup", "說明", {}, lambda 參數: 收到.append(參數))
    )
    登錄器 = 建立版本釘選工具登錄器(庫, (項目,))
    assert json.loads(登錄器.呼叫工具("lookup", {"nested": [不合法值]}))["success"] is False
    assert 收到 == []


def test_JSON快照拒絕循環且不呼叫handler():
    """循環 caller tree 不得造成無限遞迴或進入 handler。"""
    呼叫參數 = {}
    呼叫參數["cycle"] = 呼叫參數
    收到 = []
    庫 = 工具版本庫()
    項目 = 庫.登錄修訂(
        "rev-1", 工具定義("lookup", "說明", {}, lambda 參數: 收到.append(參數))
    )
    登錄器 = 建立版本釘選工具登錄器(庫, (項目,))
    assert json.loads(登錄器.呼叫工具("lookup", 呼叫參數))["success"] is False
    assert 收到 == []


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_JSON遞迴快照控制流程清理所有production_frames(monkeypatch, 例外型別):
    """遞迴快照的每一層 production frame 都清除 caller marker。"""
    marker = "RECURSIVE_SNAPSHOT_SECRET"
    中斷 = 例外型別(marker)
    目標 = {"secret": marker}
    原始取得 = 版本庫模組._取得字典項目

    def 中斷取得(字典):
        if 字典 is 目標:
            raise 中斷
        return 原始取得(字典)

    庫 = 工具版本庫()
    項目 = 庫.登錄修訂("rev-1", _工具("lookup", "bad"))
    登錄器 = 建立版本釘選工具登錄器(庫, (項目,))
    monkeypatch.setattr(版本庫模組, "_取得字典項目", 中斷取得)
    with pytest.raises(例外型別) as 錯誤:
        登錄器.呼叫工具("lookup", {"nested": [目標]})
    assert 錯誤.value is 中斷
    _assert_發布框架無標記(錯誤.tb, marker)
