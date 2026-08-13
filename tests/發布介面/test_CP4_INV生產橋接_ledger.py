"""CP4 INV invocation ledger 與 pre-model hook 生產橋接測試。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import cast

import pytest

import 繁中代理.發布介面.呼叫.編排器 as 編排模組
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef, PublishedUsage
from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫儲存錯誤
from 繁中代理.發布介面.呼叫.生產橋接 import InvocationLedger橋接
from 繁中代理.發布介面.呼叫.編排器 import (
    執行嘗試結果,
    執行嘗試紀錄收據,
    執行嘗試請求,
    外部呼叫編排器,
)


def _儲存庫(tmp_path, 呼叫識別="inv-1"):
    """建立正式 schema、端點與 pending invocation。"""
    路徑 = tmp_path / f"{呼叫識別}.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','demo','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 12, 識別碼工廠=lambda: 呼叫識別)
    儲存庫.建立已解析呼叫("ep", "ver", f"req-{呼叫識別}", {"q": 1})
    return 路徑, 儲存庫


def _狀態與事件(路徑):
    """讀取 invocation terminal 欄位與有序 attempt events。"""
    with sqlite3.connect(路徑) as 連線:
        呼叫 = 連線.execute(
            "SELECT status,output_json,error_json,usage_json FROM endpoint_invocations"
        ).fetchone()
        事件 = 連線.execute(
            "SELECT sequence_number,event_type,payload_json FROM run_events ORDER BY sequence_number"
        ).fetchall()
    return 呼叫, 事件


def test_兩次嘗試ledger依序running_append_append_succeeded(tmp_path):
    """attempt 1 無效只留 running；attempt 2 有效才成功結案並回 exact receipt。"""
    路徑, 儲存庫 = _儲存庫(tmp_path)
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-1", "req-inv-1")
    請求一, 請求二 = 執行嘗試請求(object(), {}, None, 1), 執行嘗試請求(object(), {}, None, 2)
    橋接.開始執行嘗試(呼叫, 請求一)
    收據一 = 橋接.記錄執行嘗試(呼叫, 請求一, 執行嘗試結果("success", {"bad": 1}), False)
    assert 收據一 == 執行嘗試紀錄收據("inv-1", 1, True, 1)
    assert _狀態與事件(路徑)[0][0] == "running"
    收據二 = 橋接.記錄執行嘗試(呼叫, 請求二, 執行嘗試結果("success", {"answer": 2}), True)
    assert 收據二 == 執行嘗試紀錄收據("inv-1", 2, True, 2)
    呼叫列, 事件 = _狀態與事件(路徑)
    assert 呼叫列 == ("succeeded", '{"answer":2}', None, None)
    assert [項[:2] for 項 in 事件] == [(1, "model_attempt"), (2, "model_attempt")]
    assert '"schema_valid":false' in 事件[0][2] and '"schema_valid":true' in 事件[1][2]


@pytest.mark.parametrize("結果,有效,錯誤碼", [
    (執行嘗試結果("model_timeout"), None, "model_timeout"),
    (執行嘗試結果("success", {"bad": 2}), False, "model_output_schema_invalid"),
])
def test_terminal失敗只結案一次且不保存partial_output(tmp_path, 結果, 有效, 錯誤碼):
    """typed failure 或第二次 schema invalid 皆 failed，重送回放相同安全收據。"""
    路徑, 儲存庫 = _儲存庫(tmp_path, "inv-fail")
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-fail", "req-inv-fail")
    次數 = 2 if 有效 is False else 1
    請求 = 執行嘗試請求(object(), {}, None, 次數)
    橋接.開始執行嘗試(呼叫, 執行嘗試請求(object(), {}, None, 1))
    if 次數 == 2:
        橋接.記錄執行嘗試(
            呼叫, 執行嘗試請求(object(), {}, None, 1),
            執行嘗試結果("success", {"bad": 1}), False,
        )
    收據 = 橋接.記錄執行嘗試(呼叫, 請求, 結果, 有效)
    assert (收據.invocation_id, 收據.attempt, 收據.committed) == ("inv-fail", 次數, True)
    呼叫列, 事件 = _狀態與事件(路徑)
    assert 呼叫列[0:2] == ("failed", None) and f'{{"code":"{錯誤碼}"}}' == 呼叫列[2]
    assert len(事件) == 次數
    assert 橋接.記錄執行嘗試(呼叫, 請求, 結果, 有效) == 收據
    assert len(_狀態與事件(路徑)[1]) == 次數


def test_terminal更新失敗時event同交易rollback且重試可成功(tmp_path):
    """模擬 append 後 finalize 暫時失敗，DB 不得留下半筆 event。"""
    路徑, 儲存庫 = _儲存庫(tmp_path, "inv-rollback")
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-rollback", "req-inv-rollback")
    請求 = 執行嘗試請求(object(), {}, None, 1)
    橋接.開始執行嘗試(呼叫, 請求)
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "CREATE TRIGGER temporary_finalize_failure BEFORE UPDATE OF status ON endpoint_invocations "
            "WHEN NEW.status='failed' BEGIN SELECT RAISE(ABORT,'temporary'); END"
        )
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        橋接.記錄執行嘗試(呼叫, 請求, 執行嘗試結果("model_timeout"), None)
    assert _狀態與事件(路徑) == (("running", None, None, None), [])
    with sqlite3.connect(路徑) as 連線:
        連線.execute("DROP TRIGGER temporary_finalize_failure")
    收據 = 橋接.記錄執行嘗試(呼叫, 請求, 執行嘗試結果("model_timeout"), None)
    assert 收據 == 執行嘗試紀錄收據("inv-rollback", 1, True, 1)
    assert len(_狀態與事件(路徑)[1]) == 1


def test_session附加失敗時成功狀態與event同交易rollback且可重試(tmp_path):
    """session pair INSERT 失敗不得先留下 succeeded invocation 或 attempt event。"""
    路徑, 儲存庫 = _儲存庫(tmp_path, "inv-session")
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-session", "req-inv-session", "case")
    釘選 = SimpleNamespace(endpoint_id="ep", version_id="ver", service_account_id="svc")
    請求 = 執行嘗試請求(釘選, {"q": 1}, None, 1, ())
    結果 = 執行嘗試結果("success", {"answer": 1})
    橋接.開始執行嘗試(呼叫, 請求)
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "CREATE TRIGGER temporary_session_failure BEFORE INSERT ON published_session_turn_pairs "
            "BEGIN SELECT RAISE(ABORT,'temporary'); END"
        )
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        橋接.記錄執行嘗試(呼叫, 請求, 結果, True)
    assert _狀態與事件(路徑) == (("running", None, None, None), [])
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT COUNT(*) FROM published_session_turn_pairs").fetchone() == (0,)
        連線.execute("DROP TRIGGER temporary_session_failure")
    assert 橋接.記錄執行嘗試(呼叫, 請求, 結果, True).committed is True
    assert _狀態與事件(路徑)[0][0] == "succeeded"
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT session_id,sequence_number FROM published_session_turn_pairs"
        ).fetchone() == ("case", 1)


def test_session_pair_token_count只估本輪成功pair不重複計入provider_total(tmp_path):
    """history token ledger 只估 user/assistant pair，不保存含舊 prompt 的 invocation usage。

    參數：``tmp_path`` 提供隔離 invocation/session SQLite authority。
    返回值：無；pair token count 必須小於刻意放大的 provider total_tokens。
    """
    路徑, 儲存庫 = _儲存庫(tmp_path, "inv-pair-token")
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-pair-token", "req-inv-pair-token", "case")
    釘選 = SimpleNamespace(endpoint_id="ep", version_id="ver", service_account_id="svc")
    請求 = 執行嘗試請求(釘選, {"q": "短"}, None, 1, ())
    使用量 = PublishedUsage(30_000)
    橋接.開始執行嘗試(呼叫, 請求)
    橋接.記錄執行嘗試(
        呼叫, 請求, 執行嘗試結果("success", {"answer": "短"}, 使用量), True,
    )
    with sqlite3.connect(路徑) as 連線:
        pair_token, usage_json = 連線.execute(
            "SELECT h.token_count,i.usage_json FROM published_session_turn_pairs h "
            "JOIN endpoint_invocations i ON i.id='inv-pair-token'"
        ).fetchone()
    assert 1 <= pair_token < 30_000
    assert '"total_tokens":30000' in usage_json


class _提交失敗連線:
    """代理 SQLite connection，只在 transaction context COMMIT 點注入失敗。"""

    def __init__(self, 連線):
        self._連線 = 連線

    def __getattr__(self, 名稱):
        return getattr(self._連線, 名稱)

    def __enter__(self):
        return self

    def __exit__(self, 錯誤型別, 錯誤, traceback):
        """在成功離開 transaction context 時先回滾，再模擬 COMMIT failure。

        參數：context manager 的 exception triple。
        返回值：不返回；固定拋普通 SQLite 錯誤。
        """
        if 錯誤型別 is None:
            self._連線.rollback()
            raise sqlite3.OperationalError("temporary commit failure")
        return self._連線.__exit__(錯誤型別, 錯誤, traceback)


@pytest.mark.parametrize("階段", ["audit", "completion_usage", "commit"])
def test_completion_usage_audit_commit任一失敗皆不留success或partial_pair(tmp_path, 階段):
    """逐點注入 audit、completion/usage 與 commit，整筆 terminal transaction 必須回滾。

    參數：``tmp_path`` 為隔離 DB；``階段`` 選擇三個原子提交故障點。
    返回值：無；running status、零 event 與零 session pair assertions 必須通過。
    """
    路徑, 基準儲存庫 = _儲存庫(tmp_path, f"inv-{階段}")
    呼叫 = InvocationRef(f"inv-{階段}", f"req-inv-{階段}", "case")
    釘選 = SimpleNamespace(endpoint_id="ep", version_id="ver", service_account_id="svc")
    請求 = 執行嘗試請求(釘選, {"q": 1}, None, 1, ())
    InvocationLedger橋接(基準儲存庫).開始執行嘗試(呼叫, 請求)
    if 階段 == "audit":
        with sqlite3.connect(路徑) as 連線:
            連線.execute(
                "CREATE TRIGGER temporary_audit_failure BEFORE INSERT ON run_events "
                "BEGIN SELECT RAISE(ABORT,'temporary audit'); END"
            )
    elif 階段 == "completion_usage":
        with sqlite3.connect(路徑) as 連線:
            連線.execute(
                "CREATE TRIGGER temporary_usage_failure BEFORE UPDATE OF status,usage_json "
                "ON endpoint_invocations BEGIN SELECT RAISE(ABORT,'temporary usage'); END"
            )

    def 建立連線(*參數, **命名):
        連線 = sqlite3.connect(*參數, **命名)
        return cast(sqlite3.Connection, _提交失敗連線(連線)) if 階段 == "commit" else 連線

    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 12, 連線工廠=建立連線)
    橋接 = InvocationLedger橋接(儲存庫)
    with pytest.raises(呼叫儲存錯誤):
        橋接.記錄執行嘗試(
            呼叫, 請求, 執行嘗試結果("success", {"answer": 1}), True,
        )
    assert _狀態與事件(路徑) == (("running", None, None, None), [])
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT COUNT(*) FROM published_session_turn_pairs").fetchone() == (0,)


def test_同session兩個completion以CAS恰一成功另一筆不留terminal副作用(tmp_path):
    """兩個已讀相同 sequence 的 invocation 由 BEGIN IMMEDIATE 決出一勝一明確 conflict。"""
    共同 = tmp_path / "same-session.sqlite3"
    初始化發布介面資料庫(共同)
    with sqlite3.connect(共同) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES ('svc',0,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) "
            "VALUES ('ep','owner','svc','demo','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'需求','提示','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    ids = iter(("inv-a", "inv-b"))
    儲存庫 = SQLite呼叫儲存庫(共同, 時鐘=lambda: 12, 識別碼工廠=lambda: next(ids))
    for 名稱 in ("a", "b"):
        儲存庫.建立已解析呼叫("ep", "ver", f"req-{名稱}", {"q": 名稱})
    橋接 = InvocationLedger橋接(儲存庫)
    釘選 = SimpleNamespace(endpoint_id="ep", version_id="ver", service_account_id="svc")
    項目 = []
    for 名稱 in ("a", "b"):
        呼叫 = InvocationRef(f"inv-{名稱}", f"req-{名稱}", "case")
        請求 = 執行嘗試請求(釘選, {"q": 名稱}, None, 1, ())
        橋接.開始執行嘗試(呼叫, 請求)
        項目.append((呼叫, 請求))
    def 完成(項):
        try:
            return 橋接.記錄執行嘗試(項[0], 項[1], 執行嘗試結果("success", {"ok": True}), True)
        except 呼叫儲存錯誤:
            return None
    with ThreadPoolExecutor(max_workers=2) as 執行池:
        結果 = list(執行池.map(完成, 項目))
    assert sum(值 is not None for 值 in 結果) == 1
    with sqlite3.connect(共同) as 連線:
        assert 連線.execute("SELECT COUNT(*) FROM published_session_turn_pairs").fetchone() == (1,)
        assert 連線.execute("SELECT COUNT(*) FROM run_events").fetchone() == (1,)
        assert sorted(列[0] for 列 in 連線.execute("SELECT status FROM endpoint_invocations")) == ["running", "succeeded"]


def test_相同terminal操作並行只提交一筆event並回放相同收據(tmp_path):
    """BEGIN IMMEDIATE 序列化同 operation，第二位讀者只接受 exact committed state。"""
    路徑, 儲存庫 = _儲存庫(tmp_path, "inv-concurrent")
    橋接 = InvocationLedger橋接(儲存庫)
    呼叫 = InvocationRef("inv-concurrent", "req-inv-concurrent")
    請求 = 執行嘗試請求(object(), {}, None, 1)
    結果 = 執行嘗試結果("model_timeout")
    橋接.開始執行嘗試(呼叫, 請求)
    with ThreadPoolExecutor(max_workers=2) as 執行池:
        收據 = list(執行池.map(lambda _: 橋接.記錄執行嘗試(呼叫, 請求, 結果, None), range(2)))
    assert 收據 == [執行嘗試紀錄收據("inv-concurrent", 1, True, 1)] * 2
    assert len(_狀態與事件(路徑)[1]) == 1


class _釘選:
    """供繞過 I03 的執行階段測試使用。"""


class _決策:
    """未進入開始流程時不使用的依賴型別。"""


def _編排器(開始鉤子, 模型, 驗證輸出):
    """建立執行階段編排器，固定回傳已通過 I03 的入口。"""
    編排器 = 外部呼叫編排器(
        object(), object(), object(), 解析未找到型別=LookupError, 釘選型別=_釘選,
        驗證型別=object, 驗證狀態型別=object, 階段型別=object,
        準備擷取=lambda *參數: object(), 寫入擷取=lambda *參數, **命名: "inv-hook",
        限流決策型別=_決策, 提交雙層計數=lambda *參數: None, 驗證輸入=lambda 釘選, 資料: True,
        開始執行嘗試=開始鉤子, 執行嘗試=模型, 驗證輸出=驗證輸出,
        記錄執行嘗試=lambda 呼叫, 請求, 結果, 有效: 執行嘗試紀錄收據(
            呼叫.id, 請求.attempt, True, 請求.attempt,
        ),
    )
    入口 = 編排模組.外部呼叫入口(
        EndpointRef("ep", "demo", 1), InvocationRef("inv-hook", "req"), _釘選(), None, None,
        編排模組._正規呼叫快照("{}", None),
    )
    編排器.開始 = lambda *參數: 入口
    return 編排器


def test_orchestrator只在attempt1模型前呼叫pre_hook():
    """pre-hook 必須先於第一個模型呼叫，重試 attempt 2 不得再次轉 running。"""
    順序 = []

    def 鉤子(呼叫, 請求):
        assert (呼叫.id, 請求.attempt) == ("inv-hook", 1)
        順序.append("running")

    def 模型(請求):
        順序.append(f"model-{請求.attempt}")
        return 執行嘗試結果("success", {"answer": 請求.attempt})

    有效序列 = iter((False, True))
    結果 = _編排器(鉤子, 模型, lambda 釘選, 資料: next(有效序列)).執行(
        "demo", "req", "key", {}, None, 1,
    )
    assert 結果.status_code == 200 and 順序 == ["running", "model-1", "model-2"]
