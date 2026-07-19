"""GOV SQLite owner-safe diagnostics 的分頁、授權與資料外洩回歸測試。"""

import sqlite3
from contextlib import closing

import pytest

from 繁中代理.發布介面.治理.觀測供應器 import SQLite端點觀測查詢服務, 端點觀測查詢錯誤
from 繁中代理.發布介面.治理.觀測契約 import 診斷查詢成功, 端點不可見結果
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

金鑰 = b"diagnostics-cursor-key-exact-32b!"


@pytest.fixture
def 診斷資料庫(tmp_path):
    """建立同時間 keyset、raw children 與歷史非 active endpoint。"""
    路徑 = tmp_path / "diagnostics.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 序號 in range(1, 4):
            連線.execute("INSERT INTO service_accounts VALUES(?,1,NULL)", (f"sa-{序號}",))
        for 序號, (擁有者, 狀態) in enumerate(
            (("owner-1", "active"), ("owner-2", "disabled"), ("owner-3", "archived")), 1
        ):
            端點, 版本 = f"ep-{序號}", f"ver-{序號}"
            連線.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (端點, 擁有者, f"sa-{序號}", f"slug-{序號}", 狀態, None, 1, 1, 60, 60),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (版本, 端點, 1, "需求", "系統", "[]", "[]", "{}", "rev", "{}", "{}",
                 "{}", None, "{}", 0, 擁有者, 1),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (版本, 端點))
        for 識別碼, 建立時間 in (("inv-c", 95.0), ("inv-b", 90.0), ("inv-a", 90.0), ("inv-old", 49.0)):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
                "input_json,metadata_json,output_json,error_json,usage_json,latency_ms,created_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (識別碼, "ep-1", "ver-1", f"req-{識別碼}", "failed",
                 '{"input":"RAW_INPUT"}', '{"metadata":"RAW_METADATA"}',
                 '{"output":"RAW_OUTPUT"}',
                 '{"code":"safe_code","schema_path":"$.safe","internal":"RAW_ERROR"}',
                 '{"input_tokens":2,"output_tokens":3,"total_tokens":5,"estimated_cost_usd":"0"}',
                 12.5, 建立時間, 建立時間 + 1),
            )
            連線.execute(
                "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                (f"run-{識別碼}", 識別碼, 1, "model", '{"payload":"RAW_EVENT"}', 建立時間),
            )
            for 順序, 名稱 in enumerate(("z_tool", "a_tool", "z_tool"), 1):
                成功 = 順序 < 3
                連線.execute(
                    "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,"
                    "arguments_json,outcome,result_json,error_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"tool-{識別碼}-{順序}", 識別碼, 順序, 名稱,
                     '{"arg":"RAW_ARG"}', "success" if 成功 else "error",
                     '{"result":"RAW_RESULT"}' if 成功 else None,
                     None if 成功 else '{"detail":"RAW_TOOL_ERROR"}', 建立時間),
                )
        for 端點, 版本, 時間 in (("ep-2", "ver-2", 80.0), ("ep-3", "ver-3", 81.0)):
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)", (f"inv-{端點}", 端點, 版本, f"req-{端點}", "succeeded", "{}", 時間),
            )
    return 路徑


def _服務(路徑, *, key=金鑰, clock=lambda: 100.0):
    return SQLite端點觀測查詢服務(str(路徑), 時鐘=clock, 游標簽章金鑰=key)


def _列出(服務, *, owner="owner-1", admin=False, endpoint="ep-1", limit=2, cursor=None, window=50):
    return 服務.列出端點診斷(
        擁有者使用者識別碼=owner, 是否管理者=admin, 端點識別碼=endpoint,
        視窗秒數=window, 數量上限=limit, 游標=cursor,
    )


def test_精確limit_desc_tie_order_cursor且跨頁無重複遺漏(診斷資料庫):
    服務 = _服務(診斷資料庫)
    第一頁 = _列出(服務).頁
    assert [項.invocation_id for 項 in 第一頁.items] == ["inv-c", "inv-b"]
    assert len(第一頁.items) == 2 and type(第一頁.next_cursor) is str
    第二頁 = _列出(服務, cursor=第一頁.next_cursor).頁
    assert [項.invocation_id for 項 in 第二頁.items] == ["inv-a"]
    assert 第二頁.next_cursor is None
    assert {項.invocation_id for 項 in 第一頁.items + 第二頁.items} == {"inv-a", "inv-b", "inv-c"}


def test_cursor釘選原始window不重讀clock且位置continuity(診斷資料庫):
    呼叫 = []
    def 時鐘():
        呼叫.append(1)
        return 100.0
    服務 = _服務(診斷資料庫, clock=時鐘)
    游標 = _列出(服務, limit=1).頁.next_cursor
    assert 呼叫 == [1]
    assert [項.invocation_id for 項 in _列出(服務, limit=1, cursor=游標).頁.items] == ["inv-b"]
    assert 呼叫 == [1]


@pytest.mark.parametrize(("owner", "endpoint"), (("owner-2", "ep-1"), ("owner-1", "missing")))
def test_missing與foreign為exact_typed_not_visible(診斷資料庫, owner, endpoint):
    結果 = _列出(_服務(診斷資料庫), owner=owner, endpoint=endpoint)
    assert type(結果) is 端點不可見結果


def test_admin可讀foreign且disabled_archived仍可讀歷史(診斷資料庫):
    服務 = _服務(診斷資料庫)
    管理員 = _列出(服務, owner="admin", admin=True, endpoint="ep-2")
    停用擁有者 = _列出(服務, owner="owner-2", endpoint="ep-2")
    封存擁有者 = _列出(服務, owner="owner-3", endpoint="ep-3")
    assert all(type(結果) is 診斷查詢成功 for 結果 in (管理員, 停用擁有者, 封存擁有者))
    assert [管理員.頁.items[0].invocation_id, 停用擁有者.頁.items[0].invocation_id,
            封存擁有者.頁.items[0].invocation_id] == ["inv-ep-2", "inv-ep-2", "inv-ep-3"]


def test_safe_allowlist_usage與tool_names精確(診斷資料庫):
    項 = _列出(_服務(診斷資料庫), limit=1).頁.items[0]
    assert tuple(項.__slots__) == ("invocation_id", "request_id", "endpoint_version_id", "status",
        "error_code", "schema_path", "latency_ms", "usage", "tool_names", "created_at",
        "completed_at", "redacted_fields")
    assert (項.error_code, 項.schema_path, 項.latency_ms, 項.usage.total_tokens) == (
        "safe_code", "$.safe", 12.5, 5)
    assert 項.tool_names == ("a_tool", "z_tool")
