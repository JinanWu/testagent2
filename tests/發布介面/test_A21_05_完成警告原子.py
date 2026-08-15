"""A21-05 accepted response completion 的敏感警告原子 closure。"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.呼叫.儲存庫 import (
    SQLite呼叫儲存庫,
    呼叫儲存錯誤,
    呼叫敏感交易協調器,
)
from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫
from 繁中代理.發布介面.呼叫.擷取政策 import (
    敏感偵測擷取結果,
    準備含敏感偵測的呼叫擷取,
    目標敏感命中,
    擷取階段,
)
from 繁中代理.發布介面.呼叫.生產橋接 import InvocationLedger橋接
import 繁中代理.發布介面.呼叫.編排器 as 編排模組
from 繁中代理.發布介面.呼叫.編排器 import (
    執行嘗試結果,
    執行嘗試請求,
    外部呼叫編排器,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef, PublishedWarning


def _安全標記() -> str:
    return "".join(("completion", "@", "example.test"))


def _建立資料庫(tmp_path, invocation_id="inv"):
    路徑 = tmp_path / "a21-05.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('svc',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) "
            "VALUES('ep','owner','svc','atomic','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'safe','safe','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: invocation_id)
    庫.建立已解析呼叫("ep", "ver", f"req-{invocation_id}", {})
    庫.標記執行中(invocation_id)
    return 路徑


def _協調器(路徑, detector=準備含敏感偵測的呼叫擷取):
    writer = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 11,
        識別碼工廠=iter(f"audit-{n}" for n in range(20)).__next__,
        命中識別碼工廠=iter(f"hit-{n}" for n in range(20)).__next__,
    )
    return 呼叫敏感交易協調器(writer, 偵測器=detector)


def _完成(路徑, result, *, detector=準備含敏感偵測的呼叫擷取):
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 敏感交易協調器=_協調器(路徑, detector),
    )
    return InvocationLedger橋接(庫).記錄執行嘗試(
        InvocationRef("inv", "req-inv"), 執行嘗試請求(object(), {}, None, 1),
        result, True,
    )


def _資料量(路徑):
    with sqlite3.connect(路徑) as 連線:
        status, output = 連線.execute(
            "SELECT status,output_json FROM endpoint_invocations WHERE id='inv'"
        ).fetchone()
        counts = tuple(連線.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "run_events", "invocation_sensitive_hits", "audit_events", "endpoint_redactions",
        ))
    return status, output, counts


def test_response_hit原子保存output_hit_audit_warning且restart_replay不重複(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    provider = PublishedWarning("provider_notice", "供應商已恢復。")
    forged_sensitive = PublishedWarning("sensitive_data_detected", "不可信訊息")
    data = {"z": 1, "mail": _安全標記()}
    result = 執行嘗試結果("success", data, None, (provider, forged_sensitive, provider))

    收據 = _完成(路徑, result)
    assert [(w.code, w.message) for w in 收據.warnings] == [
        ("provider_notice", "供應商已恢復。"),
        ("provider_notice", "供應商已恢復。"),
        ("sensitive_data_detected", "回應包含可能的敏感資料。"),
    ]
    with sqlite3.connect(路徑) as 連線:
        invocation = 連線.execute(
            "SELECT status,output_json,error_json,completed_at FROM endpoint_invocations WHERE id='inv'"
        ).fetchone()
        payload = json.loads(連線.execute(
            "SELECT payload_json FROM run_events WHERE invocation_id='inv'"
        ).fetchone()[0])
        hits = 連線.execute(
            "SELECT target_type,tool_call_id FROM invocation_sensitive_hits WHERE invocation_id='inv'"
        ).fetchall()
        audits = [json.loads(row[0]) for row in 連線.execute(
            "SELECT metadata_json FROM audit_events WHERE invocation_id='inv'"
        ).fetchall()]
        redactions = 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone()[0]
    assert invocation == ("succeeded", '{"mail":"' + _安全標記() + '","z":1}', None, 13)
    assert payload["warnings"] == [
        {"code": "provider_notice", "message": "供應商已恢復。"},
        {"code": "provider_notice", "message": "供應商已恢復。"},
        {"code": "sensitive_data_detected", "message": "回應包含可能的敏感資料。"},
    ]
    assert hits == [("response_data", None)] and redactions == 0
    assert len(audits) == 1 and set(audits[0]) == {
        "warning_code", "target", "detector_type", "json_path", "start", "end",
    }

    assert _完成(路徑, result) == 收據
    with sqlite3.connect(路徑) as 連線:
        assert tuple(連線.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "run_events", "invocation_sensitive_hits", "audit_events",
        )) == (1, 1, 1)


def test_response無hit不新增sensitive_warning且保留provider_warning(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    result = 執行嘗試結果(
        "success", {"answer": 1}, None,
        (PublishedWarning("sensitive_data_detected", "不可信訊息"),
         PublishedWarning("provider_notice", "供應商通知。")),
    )
    收據 = _完成(路徑, result)
    assert [(w.code, w.message) for w in 收據.warnings] == [
        ("provider_notice", "供應商通知。"),
    ]
    with sqlite3.connect(路徑) as 連線:
        payload = json.loads(連線.execute("SELECT payload_json FROM run_events").fetchone()[0])
        assert payload["warnings"] == [{"code": "provider_notice", "message": "供應商通知。"}]
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (0,)


def test_response_hit使warning超界時fail_closed且全部rollback(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    warnings = tuple(PublishedWarning(f"provider-{index}", "通知") for index in range(64))
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        _完成(路徑, 執行嘗試結果("success", {"mail": _安全標記()}, None, warnings))
    assert _資料量(路徑) == ("running", None, (0, 0, 0, 0))


def test_same_completion_identity_different_response_hit_set拒絕且不改已完成authority(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    result = 執行嘗試結果("success", {"mail": _安全標記()})
    _完成(路徑, result)
    原結果 = 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, {}, None, response_data=result.data,
    )

    def 改變偵測(*args, **kwargs):
        del args, kwargs
        return 敏感偵測擷取結果(
            原結果.命令,
            原結果.命中們 + (目標敏感命中("response_data", "phone", "/extra", 0, 1),),
            ("sensitive_data_detected",),
        )

    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        _完成(路徑, result, detector=改變偵測)
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)


@pytest.mark.parametrize("stage", ["detector", "writer", "audit", "hit", "completion", "warning"])
def test_completion任一階段失敗皆rollback且不completed_partial(tmp_path, stage):
    路徑 = _建立資料庫(tmp_path)
    if stage in {"audit", "hit", "completion", "warning"}:
        table = {"audit": "audit_events", "hit": "invocation_sensitive_hits"}.get(stage)
        if table is not None:
            sql = f"CREATE TRIGGER fail_stage BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'fixed'); END"
        elif stage == "completion":
            sql = ("CREATE TRIGGER fail_stage BEFORE UPDATE OF status ON endpoint_invocations "
                   "WHEN NEW.status='succeeded' BEGIN SELECT RAISE(ABORT,'fixed'); END")
        else:
            sql = "CREATE TRIGGER fail_stage BEFORE INSERT ON run_events BEGIN SELECT RAISE(ABORT,'fixed'); END"
        with sqlite3.connect(路徑) as 連線:
            連線.execute(sql)

    class 注入協調器:
        def 偵測呼叫(self, *_args):
            raise AssertionError

        def 偵測工具(self, *_args):
            raise AssertionError

        def 偵測回應(self, response):
            if stage == "detector":
                raise RuntimeError("fixed")
            return 準備含敏感偵測的呼叫擷取(
                擷取階段.AUTHENTICATED, {}, None, response_data=response,
            )

        def 寫入呼叫交易(self, *args, **kwargs):
            if stage == "writer":
                raise RuntimeError("fixed")
            return _協調器(路徑).寫入呼叫交易(*args, **kwargs)

    庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 13, 敏感交易協調器=注入協調器())
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$") as info:
        InvocationLedger橋接(庫).記錄執行嘗試(
            InvocationRef("inv", "req-inv"), 執行嘗試請求(object(), {}, None, 1),
            執行嘗試結果("success", {"mail": _安全標記()}), True,
        )
    assert info.value.__cause__ is None
    assert _資料量(路徑) == ("running", None, (0, 0, 0, 0))


class _提交失敗代理:
    def __init__(self, connection):
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def commit(self):
        self._connection.rollback()
        raise sqlite3.OperationalError("fixed")

    def close(self):
        return self._connection.close()


def test_completion_commit失敗不回receipt且warning_output全rollback(tmp_path):
    路徑 = _建立資料庫(tmp_path)

    def factory(*args, **kwargs):
        return _提交失敗代理(sqlite3.connect(*args, **kwargs))

    庫 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 13, 連線工廠=factory)
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        InvocationLedger橋接(庫).記錄執行嘗試(
            InvocationRef("inv", "req-inv"), 執行嘗試請求(object(), {}, None, 1),
            執行嘗試結果(
                "success", {"answer": 1}, None,
                (PublishedWarning("provider_notice", "供應商通知。"),),
            ), True,
        )
    assert _資料量(路徑) == ("running", None, (0, 0, 0, 0))


def test_same_completion_concurrency只有single_authority(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    result = 執行嘗試結果("success", {"mail": _安全標記()})
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: _完成(路徑, result), range(2)))
    assert receipts[0] == receipts[1]
    assert _資料量(路徑) == ("succeeded", '{"mail":"' + _安全標記() + '"}', (1, 1, 1, 0))


def test_schema_validator與detector看見同canonical_bytes且外部warning取自committed_receipt(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    observed = {"validator": [], "detector_before": [], "detector_after": []}

    def detector(*args, **kwargs):
        response = kwargs["response_data"]
        observed["detector_before"].append(json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode())
        result = 準備含敏感偵測的呼叫擷取(*args, **kwargs)
        observed["detector_after"].append(json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode())
        return result

    repository = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 敏感交易協調器=_協調器(路徑, detector),
    )
    bridge = InvocationLedger橋接(repository)

    def validate(_pin, data):
        observed["validator"].append(json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode())
        return True

    orchestrator = 外部呼叫編排器(
        object(), object(), object(), 解析未找到型別=LookupError, 釘選型別=object,
        驗證型別=object, 驗證狀態型別=object, 階段型別=object,
        準備擷取=lambda *_args: object(), 寫入擷取=lambda *_args, **_kwargs: "inv",
        限流決策型別=object, 提交雙層計數=lambda *_args: object(),
        驗證輸入=lambda *_args: True,
        執行嘗試=lambda _request: 執行嘗試結果(
            "success", {"z": [1], "mail": _安全標記()}, None,
            (PublishedWarning("provider_notice", "供應商通知。"),),
        ),
        驗證輸出=validate, 記錄執行嘗試=bridge.記錄執行嘗試,
    )
    entry = 編排模組.外部呼叫入口(
        EndpointRef("ep", "atomic", 1), InvocationRef("inv", "req-inv"), object(), None, None,
        編排模組._正規呼叫快照("{}", None),
    )
    orchestrator.開始 = lambda *_args: entry
    payload = orchestrator.執行("atomic", "req-inv", "key", {}, None, 1).to_json()

    assert payload["status_code"] == 200
    assert payload["envelope"]["data"] == {"mail": _安全標記(), "z": [1]}
    assert payload["envelope"]["warnings"] == [
        {"code": "provider_notice", "message": "供應商通知。"},
        {"code": "sensitive_data_detected", "message": "回應包含可能的敏感資料。"},
    ]
    assert observed["validator"] == observed["detector_before"] == observed["detector_after"]
