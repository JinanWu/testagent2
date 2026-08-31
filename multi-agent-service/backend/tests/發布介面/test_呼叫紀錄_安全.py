"""呼叫紀錄的快照、schema 與並行安全回歸。"""

import json
import sqlite3
import threading

import pytest

import 繁中代理.發布介面.呼叫.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫儲存錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立資料庫(tmp_path):
    """建立已完整遷移且含可呼叫端點版本的資料庫。"""
    路徑 = tmp_path / "safe-invocations.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints("
            "id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','safe','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _讀取列(路徑, invocation_id):
    """以新連線讀取指定呼叫並回傳 dict。"""
    with sqlite3.connect(路徑) as 連線:
        連線.row_factory = sqlite3.Row
        return dict(連線.execute(
            "SELECT * FROM endpoint_invocations WHERE id=?", (invocation_id,)
        ).fetchone())


def _執行緒呼叫(動作, 結果):
    """執行動作並將成功或例外物件存入共享結果。"""
    try:
        動作()
        結果.append(None)
    except BaseException as 錯誤:
        結果.append(錯誤)


def _同時執行(*動作們):
    """以 Barrier 同時放行多個真實執行緒並回傳各動作結果。"""
    閘門 = threading.Barrier(len(動作們))
    結果 = []

    def 包裝(動作):
        """等待共同閘門後執行單一動作。"""
        閘門.wait()
        _執行緒呼叫(動作, 結果)

    執行緒們 = [threading.Thread(target=包裝, args=(動作,)) for 動作 in 動作們]
    for 執行緒 in 執行緒們:
        執行緒.start()
    for 執行緒 in 執行緒們:
        執行緒.join(timeout=10)
    assert not any(執行緒.is_alive() for 執行緒 in 執行緒們)
    return 結果


def _完成指定終態(儲存庫, invocation_id, status):
    """以符合成功或失敗形狀的最小 payload 完成指定呼叫。"""
    if status == "succeeded":
        儲存庫.完成呼叫(invocation_id, status, output={})
    else:
        儲存庫.完成呼叫(invocation_id, status, error={})


def test_建立只正規化已脫離呼叫者的input與metadata快照(tmp_path, monkeypatch):
    """Event 夾在快照與正規化間修改原物件，資料庫仍只保存修改前樹。"""
    路徑 = _建立資料庫(tmp_path)
    input值 = {"nested": ["before"]}
    metadata值 = {"tag": ["before"]}
    已進正規器 = threading.Event()
    可繼續 = threading.Event()
    原正規器 = 儲存庫模組.建立正規JSON
    呼叫次數 = 0

    def 暫停正規器(值):
        """第一次正規化前通知測試執行緒並等待原物件完成突變。"""
        nonlocal 呼叫次數
        呼叫次數 += 1
        if 呼叫次數 == 1:
            已進正規器.set()
            assert 可繼續.wait(timeout=10)
        return 原正規器(值)

    monkeypatch.setattr(儲存庫模組, "建立正規JSON", 暫停正規器)
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "create-snapshot")
    結果 = []
    執行緒 = threading.Thread(
        target=_執行緒呼叫,
        args=(lambda: 儲存庫.建立已解析呼叫(
            "ep", "ver", "req-create-snapshot", input值, metadata=metadata值
        ), 結果),
    )
    執行緒.start()
    assert 已進正規器.wait(timeout=10)
    input值["nested"].append("after")
    metadata值["tag"].append("after")
    可繼續.set()
    執行緒.join(timeout=10)

    assert 結果 == [None]
    資料列 = _讀取列(路徑, "create-snapshot")
    assert json.loads(資料列["input_json"]) == {"nested": ["before"]}
    assert json.loads(資料列["metadata_json"]) == {"tag": ["before"]}


@pytest.mark.parametrize(
    ("status", "欄位"),
    [("succeeded", "output_json"), ("failed", "error_json")],
)
def test_結案只正規化已脫離呼叫者的payload與usage快照(tmp_path, monkeypatch, status, 欄位):
    """Event 修改結案原payload與usage時，output/error/usage皆保存修改前快照。"""
    路徑 = _建立資料庫(tmp_path)
    invocation_id = f"finish-{status}"
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 2, 識別碼工廠=lambda: invocation_id)
    儲存庫.建立已解析呼叫("ep", "ver", f"req-{status}", {})
    if status == "succeeded":
        儲存庫.標記執行中(invocation_id)
    payload = {"nested": ["before"]}
    usage = {"tokens": [1]}
    已進正規器 = threading.Event()
    可繼續 = threading.Event()
    原正規器 = 儲存庫模組.建立正規JSON

    def 暫停正規器(值):
        """在第一個可信結案快照正規化時等待呼叫者突變。"""
        if not 已進正規器.is_set():
            已進正規器.set()
            assert 可繼續.wait(timeout=10)
        return 原正規器(值)

    monkeypatch.setattr(儲存庫模組, "建立正規JSON", 暫停正規器)
    kwargs = {"output" if status == "succeeded" else "error": payload, "usage": usage}
    結果 = []
    執行緒 = threading.Thread(
        target=_執行緒呼叫,
        args=(lambda: 儲存庫.完成呼叫(invocation_id, status, **kwargs), 結果),
    )
    執行緒.start()
    assert 已進正規器.wait(timeout=10)
    payload["nested"].append("after")
    usage["tokens"].append(2)
    可繼續.set()
    執行緒.join(timeout=10)

    assert 結果 == [None]
    資料列 = _讀取列(路徑, invocation_id)
    assert json.loads(資料列[欄位]) == {"nested": ["before"]}
    assert json.loads(資料列["usage_json"]) == {"tokens": [1]}


def test_runtime拒絕同欄位但移除check的schema(tmp_path):
    """竄改 sqlite_master 移除 request_id CHECK 後，欄位未變也不得開啟。"""
    路徑 = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        原欄位 = tuple(row[1] for row in 連線.execute("PRAGMA table_info(endpoint_invocations)"))
        SQL = 連線.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_invocations'"
        ).fetchone()[0]
        竄改SQL = SQL.replace(" CHECK(trim(request_id) <> '')", "")
        版本 = 連線.execute("PRAGMA schema_version").fetchone()[0]
        連線.execute("PRAGMA writable_schema=ON")
        連線.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='endpoint_invocations'",
            (竄改SQL,),
        )
        連線.execute("PRAGMA writable_schema=OFF")
        連線.execute(f"PRAGMA schema_version={版本 + 1}")
    with sqlite3.connect(路徑) as 連線:
        assert tuple(row[1] for row in 連線.execute(
            "PRAGMA table_info(endpoint_invocations)"
        )) == 原欄位

    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        SQLite呼叫儲存庫(路徑).建立已解析呼叫("ep", "ver", "req-bad-schema", {})


def test_兩連線競爭轉換與結案不遺失更新或雙重完成(tmp_path):
    """真實雙執行緒各開連線競爭 pending 及 running，只能有一個勝者。"""
    路徑 = _建立資料庫(tmp_path)
    建立庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 3, 識別碼工廠=lambda: "race-transition")
    建立庫.建立已解析呼叫("ep", "ver", "req-race-transition", {})
    庫一 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 4)
    庫二 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 5)
    結果 = _同時執行(
        lambda: 庫一.標記執行中("race-transition"),
        lambda: 庫二.完成呼叫("race-transition", "invalid_api_key", error={"winner": "terminal"}),
    )
    assert sum(項目 is None for 項目 in 結果) == 1
    assert _讀取列(路徑, "race-transition")["status"] in {"running", "invalid_api_key"}

    建立庫._識別碼工廠 = lambda: "race-finalize"
    建立庫.建立已解析呼叫("ep", "ver", "req-race-finalize", {})
    建立庫.標記執行中("race-finalize")
    結果 = _同時執行(
        lambda: 庫一.完成呼叫("race-finalize", "succeeded", output={"winner": "success"}),
        lambda: 庫二.完成呼叫("race-finalize", "failed", error={"winner": "failure"}),
    )
    assert sum(項目 is None for 項目 in 結果) == 1
    assert _讀取列(路徑, "race-finalize")["status"] in {"succeeded", "failed"}


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "rate_limited", "invalid_api_key"])
def test_終態與running的完整禁止再轉換矩陣(tmp_path, terminal):
    """running不可重入或限流/金鑰拒絕，且每一終態不可再轉換或結案。"""
    路徑 = _建立資料庫(tmp_path)
    invocation_id = f"matrix-{terminal}"
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 6, 識別碼工廠=lambda: invocation_id)
    儲存庫.建立已解析呼叫("ep", "ver", f"req-matrix-{terminal}", {})
    if terminal in {"succeeded", "failed"}:
        儲存庫.標記執行中(invocation_id)
        with pytest.raises(呼叫儲存錯誤):
            儲存庫.標記執行中(invocation_id)
        for forbidden in ("rate_limited", "invalid_api_key"):
            with pytest.raises(呼叫儲存錯誤):
                儲存庫.完成呼叫(invocation_id, forbidden, error={})
    _完成指定終態(儲存庫, invocation_id, terminal)

    with pytest.raises(呼叫儲存錯誤):
        儲存庫.標記執行中(invocation_id)
    for 再結案 in ("succeeded", "failed", "rate_limited", "invalid_api_key"):
        with pytest.raises(呼叫儲存錯誤):
            _完成指定終態(儲存庫, invocation_id, 再結案)
