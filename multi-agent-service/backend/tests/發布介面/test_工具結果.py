"""canonical 單層工具結果與 legacy 登錄器相容性。"""

import json
import math
import threading

import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.工具 import 回報工具未啟用, 工具定義, 工具登錄器
from 繁中代理.發布介面.執行期 import 工具結果 as 工具結果模組
from 繁中代理.發布介面.執行期.工具結果 import (
    可恢復工具錯誤,
    工具執行結果,
    工具設定錯誤,
    工具逾時,
    呼叫工具處理函數,
)


class 自訂鍵盤中斷(KeyboardInterrupt):
    pass


class 自訂系統離開(SystemExit):
    pass


class 自訂產生器離開(GeneratorExit):
    pass


控制流程類別 = (
    KeyboardInterrupt, 自訂鍵盤中斷,
    SystemExit, 自訂系統離開,
    GeneratorExit, 自訂產生器離開,
)


def _呼叫(回傳=None, 例外=None):
    def 處理(參數):
        if 例外 is not None:
            raise 例外
        return 回傳

    return 呼叫工具處理函數(處理, {"value": 1})


def _物件(結果):
    return 結果.轉成JSON物件()


def _assert_工具框架無標記(traceback, marker):
    """遞迴檢查工具 production frame 不可保留控制流程 secret。"""
    def 含標記(值, 已看):
        if id(值) in 已看:
            return False
        已看.add(id(值))
        if type(值) is str:
            return marker in 值
        if type(值) is dict:
            return any(含標記(k, 已看) or 含標記(v, 已看) for k, v in 值.items())
        if type(值) in (tuple, list, set, frozenset):
            return any(含標記(v, 已看) for v in 值)
        if isinstance(值, BaseException):
            return 含標記(值.args, 已看)
        if hasattr(值, "__dict__"):
            return 含標記(vars(值), 已看)
        return False

    while traceback:
        frame = traceback.tb_frame
        模組 = frame.f_globals.get("__name__", "")
        if 模組 in ("繁中代理.工具", "繁中代理.發布介面.執行期.工具結果"):
            assert not any(含標記(v, set()) for v in frame.f_locals.values()), frame.f_code.co_name
        traceback = traceback.tb_next


@pytest.mark.parametrize("值", [None, True, 3, 1.5, "文字", [1], {"a": [2]}])
def test_普通JSON值成為單層成功且脫離caller(值):
    結果 = _呼叫(值)
    assert _物件(結果) == {"success": True, "result": 值}
    assert json.loads(結果.轉成正規JSON()) == _物件(結果)
    if type(值) in (list, dict):
        值.clear()
        assert _物件(結果) != {"success": True, "result": 值}
        公開 = _物件(結果)
        公開["result"].clear()
        assert _物件(結果)["result"]


def test_legacy成功與失敗只正規化一次且錯誤固定():
    assert _物件(_呼叫({"success": True, "result": {"x": 1}})) == {
        "success": True, "result": {"x": 1}
    }
    失敗 = _物件(_呼叫({"success": False, "error": "SECRET/path"}))
    assert 失敗 == {
        "success": False, "error": "工具執行失敗",
        "code": "tool_execution_failed", "recoverable": False,
    }
    assert "SECRET" not in repr(_呼叫({"success": False, "error": "SECRET"}))


@pytest.mark.parametrize("值", [
    {"success": True, "error": "bad"},
    {"success": False, "error": "bad", "result": 1},
    {"success": False}, {"success": 1},
    {"success": True, "result": {"success": False, "error": "nested"}},
])
def test_矛盾或巢狀outcome失敗關閉(值):
    結果 = _物件(_呼叫(值))
    assert 結果["success"] is False
    assert 結果["code"] == "endpoint_misconfigured"
    assert json.dumps(結果).count('"success"') == 1


@pytest.mark.parametrize("值", [object(), float("nan"), float("inf"), b"bytes"])
def test_非嚴格JSON固定為設定錯誤(值):
    assert _物件(_呼叫(值))["code"] == "endpoint_misconfigured"


def test_循環深度節點與subclass固定拒絕():
    循環 = []
    循環.append(循環)
    過深 = 0
    for _ in range(70):
        過深 = [過深]

    class 惡意字典(dict):
        pass

    for 值 in (循環, 過深, list(range(10001)), 惡意字典(a=1)):
        assert _物件(_呼叫(值))["code"] == "endpoint_misconfigured"


def test_例外分類固定且不洩漏原文():
    矩陣 = [
        (工具逾時("APIKEY /Users/secret"), "tool_timeout", False),
        (TimeoutError("APIKEY /Users/secret"), "tool_execution_failed", False),
        (工具設定錯誤("APIKEY /Users/secret"), "endpoint_misconfigured", False),
        (可恢復工具錯誤("APIKEY /Users/secret"), "tool_execution_failed", True),
        (RuntimeError("APIKEY /Users/secret"), "tool_execution_failed", False),
    ]
    for 例外, code, recoverable in 矩陣:
        結果 = _呼叫(例外=例外)
        公開 = 結果.轉成正規JSON()
        assert json.loads(公開)["code"] == code
        assert json.loads(公開)["recoverable"] is recoverable
        assert "APIKEY" not in 公開 + repr(結果)


def test_自訂BaseException固定失敗():
    class 惡意錯誤(BaseException):
        pass

    assert _物件(_呼叫(例外=惡意錯誤("SECRET")))["code"] == "tool_execution_failed"


@pytest.mark.parametrize("類別", 控制流程類別)
def test_handler直接控制流程基類與子類identity_args及框架清理(類別):
    marker = "HANDLER-CONTROL-SECRET"
    原例外 = 類別(marker)
    with pytest.raises(類別) as 捕捉:
        _呼叫(例外=原例外)
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _assert_工具框架無標記(捕捉.tb, marker)


@pytest.mark.parametrize("類別", 控制流程類別)
def test_登錄器控制流程identity_args與所有工具frame清理(類別):
    marker = "CONTROL/SECRET/PATH"
    原例外 = 類別(marker)
    登錄器 = 工具登錄器()

    def 處理(參數):
        raise 原例外

    登錄器.登錄工具(工具定義("interrupt", marker, {}, 處理))
    with pytest.raises(類別) as 捕捉:
        登錄器.呼叫工具("interrupt", {"marker": marker})
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _assert_工具框架無標記(捕捉.tb, marker)


def test_DTO不可變且偽造欄位會在序列化時重建():
    結果 = _呼叫({"a": 1})
    內部 = object.__getattribute__(結果, "_result_json")
    assert type(內部) is str and json.loads(內部) == {"a": 1}
    公開結果 = 結果.result
    公開結果["a"] = 9
    assert 結果.result == {"a": 1}
    with pytest.raises((AttributeError, TypeError)):
        結果.success = False
    繞過公開 = object.__getattribute__(結果, "result")
    繞過公開.clear()
    assert 結果.result == {"a": 1}
    object.__setattr__(結果, "_result_json", object())
    公開 = 結果.轉成JSON物件()
    assert 公開["success"] is False
    assert 公開["code"] == "endpoint_misconfigured"


@pytest.mark.parametrize("偽造", ['{"b":1,"a":2}', '{"a":1,"a":2}', '[NaN]'])
def test_DTO偽造非canonical內部結果固定拒絕(偽造):
    結果 = _呼叫({"a": 1})
    object.__setattr__(結果, "_result_json", 偽造)
    assert 結果.轉成JSON物件()["code"] == "endpoint_misconfigured"


def test_授權前捕捉handler與深層參數快照():
    原始參數 = {"nested": ["original"]}
    呼叫 = []
    登錄器 = 工具登錄器()

    def 原處理(參數):
        呼叫.append(("original", 參數["nested"][0]))
        return list(呼叫[-1])

    def 惡意處理(參數):
        呼叫.append(("evil", 參數["nested"][0]))
        return list(呼叫[-1])

    工具 = 工具定義("target", "", {}, 原處理)
    登錄器.登錄工具(工具)

    class 惡意上下文(使用者上下文):
        def 工具是否允許(self, 名稱):
            原始參數["nested"][0] = "mutated"
            object.__setattr__(工具, "處理函數", 惡意處理)
            登錄器.工具表[名稱] = 工具定義(名稱, "", {}, 惡意處理)
            return True

    登錄器.使用者上下文物件 = 惡意上下文()
    assert json.loads(登錄器.呼叫工具("target", 原始參數))["result"] == ["original", "original"]
    assert 呼叫 == [("original", "original")]


@pytest.mark.parametrize("類別", 控制流程類別)
@pytest.mark.parametrize("目標", ["_成功或設定錯誤", "_複製JSON", "_嚴格解碼結果", "_正規化結果文字"])
def test_正規化與重建下游控制流程清理(monkeypatch, 類別, 目標):
    marker = f"SECRET-CONTROL-{目標}"
    原例外 = 類別(marker)

    def 攔截(*args, **kwargs):
        raise 原例外

    if 目標 in ("_成功或設定錯誤", "_複製JSON"):
        monkeypatch.setattr(工具結果模組, 目標, 攔截)
        動作 = lambda: 工具結果模組._正規化回傳({"handler": "SECRET-HANDLER"})
    else:
        結果 = 工具執行結果(True, {"dto": "SECRET-DTO"})
        monkeypatch.setattr(工具結果模組, 目標, 攔截)
        動作 = lambda: 工具結果模組._重建結果(結果)
    with pytest.raises(類別) as 捕捉:
        動作()
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _assert_工具框架無標記(捕捉.tb, "SECRET-")


@pytest.mark.parametrize("類別", 控制流程類別)
def test_授權callback控制流程清理(類別):
    marker = "AUTH-CONTROL-SECRET"
    原例外 = 類別(marker)

    class 中斷上下文(使用者上下文):
        def 工具是否允許(self, 名稱):
            raise 原例外

    登錄器 = 工具登錄器(使用者上下文物件=中斷上下文())
    登錄器.登錄工具(工具定義("target", "", {}, lambda 參數: pytest.fail("不可呼叫")))
    with pytest.raises(類別) as 捕捉:
        登錄器.呼叫工具("target", {"secret": marker})
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _assert_工具框架無標記(捕捉.tb, marker)


@pytest.mark.parametrize("類別", 控制流程類別)
@pytest.mark.parametrize("邊界", ["建構", "屬性", "表示", "JSON物件", "JSON字串"])
def test_DTO各序列化邊界控制流程基類與子類原樣通過(monkeypatch, 類別, 邊界):
    marker = f"DTO-SECRET-{邊界}"
    原例外 = 類別(marker)

    def 攔截(*args, **kwargs):
        raise 原例外

    if 邊界 == "建構":
        monkeypatch.setattr(工具結果模組, "_複製JSON", 攔截)
        動作 = lambda: 工具執行結果(True, {"secret": marker})
    else:
        結果 = 工具執行結果(True, {"secret": marker})
        if 邊界 == "屬性":
            monkeypatch.setattr(工具結果模組, "_驗證內部結果", 攔截)
            動作 = lambda: 結果.result
        elif 邊界 == "表示":
            class 物件代理:
                __getattribute__ = staticmethod(攔截)

            monkeypatch.setattr(工具結果模組, "object", 物件代理, raising=False)
            動作 = lambda: repr(結果)
        elif 邊界 == "JSON物件":
            monkeypatch.setattr(工具結果模組, "_重建結果", 攔截)
            動作 = 結果.轉成JSON物件
        else:
            monkeypatch.setattr(工具結果模組.json, "dumps", 攔截)
            動作 = 結果.轉成正規JSON
    with pytest.raises(類別) as 捕捉:
        動作()
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _assert_工具框架無標記(捕捉.tb, marker)


def test_工具逾時子類維持精確taxonomy而視為一般執行失敗():
    class 衍生工具逾時(工具逾時):
        pass

    公開 = _物件(_呼叫(例外=衍生工具逾時("SECRET")))
    assert 公開["code"] == "tool_execution_failed"


def test_handler恰呼叫一次且並行隔離():
    鎖 = threading.Lock()
    次數 = 0

    def 處理(參數):
        nonlocal 次數
        with 鎖:
            次數 += 1
        return {"seen": 參數["value"]}

    結果 = []
    執行緒們 = [threading.Thread(target=lambda n=n: 結果.append(
        _物件(呼叫工具處理函數(處理, {"value": n})))) for n in range(12)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    for 執行緒 in 執行緒們:
        執行緒.join(2)
    assert 次數 == 12
    assert sorted(項["result"]["seen"] for 項 in 結果) == list(range(12))


def test_登錄器legacy_false未知權限與未啟用維持單層():
    登錄器 = 工具登錄器(已知工具名稱集合={"denied"})
    登錄器.登錄工具(工具定義("bad", "", {}, lambda 參數: {
        "success": False, "error": "untrusted", "permission_denied": True,
    }))
    失敗 = json.loads(登錄器.呼叫工具("bad", {}))
    assert 失敗["success"] is False and 失敗["permission_denied"] is True
    assert "無權" in 失敗["error"] and "result" not in 失敗
    未知 = json.loads(登錄器.呼叫工具("missing", {"secret": "x"}))
    assert 未知["code"] == "endpoint_misconfigured" and "未知工具" in 未知["error"]
    權限 = json.loads(登錄器.呼叫工具("denied", {"secret": "x"}))
    assert 權限["permission_denied"] is True and "無權" in 權限["error"]

    登錄器.登錄工具(工具定義("off", "", {}, 回報工具未啟用("off")))
    未啟用 = json.loads(登錄器.呼叫工具("off", {"APIKEY": "secret"}))
    assert 未啟用["success"] is False and "received_args" not in 未啟用
    assert "APIKEY" not in json.dumps(未啟用)


def test_登錄器成功不產生nested_result且序列化錯誤受控():
    登錄器 = 工具登錄器()
    登錄器.登錄工具(工具定義("ok", "", {}, lambda 參數: {
        "success": True, "message": "完成",
    }))
    登錄器.登錄工具(工具定義("bad", "", {}, lambda 參數: math.nan))
    assert json.loads(登錄器.呼叫工具("ok", {})) == {
        "success": True, "result": {"message": "完成"}
    }
    assert json.loads(登錄器.呼叫工具("bad", {}))["code"] == "endpoint_misconfigured"
