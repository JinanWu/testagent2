"""LOG L02 執行事件與工具呼叫 append-only ledger 測試。"""

import json
import sqlite3
import threading

import pytest

import 繁中代理.發布介面.呼叫.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫儲存錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立執行中呼叫(tmp_path, invocation_id="inv"):
    """建立完整schema、端點與一筆running invocation。"""
    路徑 = tmp_path / "l02.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints("
            "id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','slug','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
        連線.execute(
            "INSERT INTO endpoint_invocations("
            "id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) "
            "VALUES (?,?,?,'req','running','{}',0)",
            (invocation_id, "ep", "ver"),
        )
    return 路徑


def _資料列們(路徑, 表格):
    """依序號讀取ledger列並回傳dict。"""
    with sqlite3.connect(f"file:{路徑}?mode=ro", uri=True) as 連線:
        連線.row_factory = sqlite3.Row
        return [dict(列) for 列 in 連線.execute(f"SELECT * FROM {表格} ORDER BY sequence_number")]


def test_事件與工具結果保存精確欄位及單層outcome(tmp_path):
    """事件及工具成功/失敗各保存一次canonical input/output/error。"""
    路徑 = _建立執行中呼叫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 8)

    assert 儲存庫.附加執行事件("inv", "evt-1", "model.started", {"z": 2, "a": 1}) == 1
    assert 儲存庫.附加工具呼叫(
        "inv", "call-1", "search_files", {"path": "."}, "success",
        result={"files": ["a"]}, run_event_id="evt-1", latency_ms=2,
    ) == 1
    assert 儲存庫.附加工具呼叫(
        "inv", "call-2", "terminal", {"command": "false"}, "error",
        error={"code": "failed"}, retry_of_tool_call_id="call-1",
    ) == 2

    事件 = _資料列們(路徑, "run_events")[0]
    工具們 = _資料列們(路徑, "endpoint_tool_calls")
    assert (事件["id"], 事件["event_type"], 事件["payload_json"]) == (
        "evt-1", "model.started", '{"a":1,"z":2}',
    )
    assert (工具們[0]["id"], 工具們[0]["tool_name"], 工具們[0]["arguments_json"]) == (
        "call-1", "search_files", '{"path":"."}',
    )
    assert json.loads(工具們[0]["result_json"]) == {"files": ["a"]}
    assert 工具們[0]["error_json"] is None
    assert json.loads(工具們[1]["error_json"]) == {"code": "failed"}
    assert 工具們[1]["result_json"] is None


@pytest.mark.parametrize(
    ("outcome", "kwargs"),
    [
        ("success", {}),
        ("success", {"result": {}, "error": {}}),
        ("error", {}),
        ("error", {"result": {}, "error": {}}),
        ("unknown", {"result": {}}),
    ],
)
def test_矛盾或缺少的工具outcome固定拒絕且不留資料(tmp_path, outcome, kwargs):
    """R80單層矩陣不接受雙結果、缺結果或未知status。"""
    路徑 = _建立執行中呼叫(tmp_path)
    with pytest.raises(呼叫儲存錯誤, match="工具呼叫附加失敗"):
        SQLite呼叫儲存庫(路徑).附加工具呼叫(
            "inv", "call", "tool", {}, outcome, **kwargs,
        )
    assert _資料列們(路徑, "endpoint_tool_calls") == []


@pytest.mark.parametrize("狀態", ["pending", "succeeded", "failed", "rate_limited", "invalid_api_key"])
@pytest.mark.parametrize("種類", ["事件", "工具"])
def test_只有running呼叫可附加且不存在亦拒絕(tmp_path, 狀態, 種類):
    """不存在及非running lifecycle不可產生後來的ledger資料。"""
    路徑 = _建立執行中呼叫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET status=?", (狀態,))
    儲存庫 = SQLite呼叫儲存庫(路徑)
    if 種類 == "事件":
        動作 = lambda: 儲存庫.附加執行事件("inv", "evt", "type", {})
    else:
        動作 = lambda: 儲存庫.附加工具呼叫("inv", "call", "tool", {}, "success", result={})
    with pytest.raises(呼叫儲存錯誤):
        動作()
    assert _資料列們(路徑, "run_events") + _資料列們(路徑, "endpoint_tool_calls") == []


def test_跨呼叫事件與重試識別不混用且交易回滾(tmp_path):
    """複合FK要求事件、retry call和目前invocation身分相同。"""
    路徑 = _建立執行中呼叫(tmp_path, "inv-1")
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) "
            "VALUES ('inv-2','ep','ver','req-2','running','{}',0)"
        )
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1)
    儲存庫.附加執行事件("inv-1", "evt-1", "type", {})
    儲存庫.附加工具呼叫("inv-1", "call-1", "tool", {}, "success", result={})
    for kwargs in ({"run_event_id": "evt-1"}, {"retry_of_tool_call_id": "call-1"}):
        with pytest.raises(呼叫儲存錯誤):
            儲存庫.附加工具呼叫("inv-2", "bad", "tool", {}, "success", result={}, **kwargs)
    assert _資料列們(路徑, "endpoint_tool_calls")[-1]["id"] == "call-1"


@pytest.mark.parametrize("種類", ["事件", "工具"])
def test_真實並行寫者配置不重複且完整單調(tmp_path, 種類):
    """獨立連線同時附加時，BEGIN IMMEDIATE序列配置無duplicate或lost ordering。"""
    路徑 = _建立執行中呼叫(tmp_path)
    閘門 = threading.Barrier(9)
    結果 = []
    錯誤 = []

    def 寫入(編號):
        儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 編號)
        閘門.wait()
        try:
            if 種類 == "事件":
                序號 = 儲存庫.附加執行事件("inv", f"evt-{編號}", "type", {"n": 編號})
            else:
                序號 = 儲存庫.附加工具呼叫(
                    "inv", f"call-{編號}", "tool", {"n": 編號}, "success", result={"n": 編號},
                )
            結果.append(序號)
        except BaseException as 例外:
            錯誤.append(例外)

    執行緒們 = [threading.Thread(target=寫入, args=(編號,)) for 編號 in range(8)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    閘門.wait()
    for 執行緒 in 執行緒們:
        執行緒.join()
    表格 = "run_events" if 種類 == "事件" else "endpoint_tool_calls"
    assert 錯誤 == []
    assert sorted(結果) == list(range(1, 9))
    assert [列["sequence_number"] for 列 in _資料列們(路徑, 表格)] == list(range(1, 9))


@pytest.mark.parametrize("種類", ["事件", "工具"])
def test_畸形SQLite動態狀態與非有限值固定拒絕(tmp_path, 種類):
    """狀態型別及數值皆須精確，不能倚賴SQLite affinity或CHECK。"""
    路徑 = _建立執行中呼叫(tmp_path)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: float("nan"))
    if 種類 == "事件":
        動作 = lambda: 儲存庫.附加執行事件("inv", "evt", "type", {})
    else:
        動作 = lambda: 儲存庫.附加工具呼叫(
            "inv", "call", "tool", {}, "success", result={}, latency_ms=float("inf"),
        )
    with pytest.raises(呼叫儲存錯誤):
        動作()
    assert _資料列們(路徑, "run_events") + _資料列們(路徑, "endpoint_tool_calls") == []


@pytest.mark.parametrize("種類", ["事件", "工具"])
def test_正規化競態保存脫離呼叫者的快照(tmp_path, monkeypatch, 種類):
    """canonicalizer只收到validation同次走訪建成的樹，後續突變不影響ledger。"""
    路徑 = _建立執行中呼叫(tmp_path)
    payload = {"outer": [{"value": "before"}]}
    已進入 = threading.Event()
    放行 = threading.Event()
    原正規器 = 儲存庫模組.建立正規JSON

    def 閘門正規器(值):
        已進入.set()
        assert 放行.wait(5)
        return 原正規器(值)

    monkeypatch.setattr(儲存庫模組, "建立正規JSON", 閘門正規器)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1)
    錯誤 = []

    def 寫入():
        try:
            if 種類 == "事件":
                儲存庫.附加執行事件("inv", "evt", "type", payload)
            else:
                儲存庫.附加工具呼叫("inv", "call", "tool", payload, "success", result={})
        except BaseException as 例外:
            錯誤.append(例外)

    執行緒 = threading.Thread(target=寫入)
    執行緒.start()
    assert 已進入.wait(5)
    payload["outer"][0]["value"] = "after"
    放行.set()
    執行緒.join()
    表格, 欄位 = (("run_events", "payload_json") if 種類 == "事件"
                 else ("endpoint_tool_calls", "arguments_json"))
    assert 錯誤 == []
    assert json.loads(_資料列們(路徑, 表格)[0][欄位]) == {"outer": [{"value": "before"}]}


def test_既有畸形序號使下一筆fail_closed且不覆寫(tmp_path):
    """ignore CHECK 寫入REAL序號後，MAX動態型別驗證拒絕配置下一號。"""
    路徑 = _建立執行中呼叫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("INSERT INTO run_events VALUES ('bad','inv',1.5,'type','{}',0)")
    with pytest.raises(呼叫儲存錯誤, match="執行事件附加失敗"):
        SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1).附加執行事件(
            "inv", "evt", "type", {},
        )
    assert [列["id"] for 列 in _資料列們(路徑, "run_events")] == ["bad"]
