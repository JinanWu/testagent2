"""A21-06 canonical production sensitive wiring 與 live lifespan restart closure。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import asgi as root_asgi
from 繁中代理.發布介面 import 生產Published執行 as 執行模組
from a08_3_formal_publish import 建立正式v1
from a08_3_stable_support import _設定環境
from production_spa_support import 建立ProductionDist
from 繁中代理.模型供應商 import GeminiADC供應商
from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫敏感交易協調器
from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫
from 繁中代理.發布介面.呼叫.生產橋接 import InvocationLedger橋接
from 繁中代理.發布介面.生產Published執行 import 生產Published執行資源
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照


def _安全標記們() -> tuple[str, str, str, str, str]:
    """Marker 只在 code 中合成，測試輸出不列印原值。"""
    郵件 = lambda 前綴: 前綴 + chr(64) + "safe.invalid"
    return (郵件("input"), "0912" + "-345-678", "4" + "1" * 15,
            郵件("arguments"), 郵件("result"))


class _五目標模型:
    """透過真實 skills tools 產生兩個 tool rows 與最終 schema response。"""

    def __init__(self, 工具參數標記: str, 回應標記: str) -> None:
        self._工具參數標記 = 工具參數標記
        self._回應標記 = 回應標記
        self.calls: list[dict[str, object]] = []

    def 產生(self, **參數: object) -> 模型回應快照:
        self.calls.append(參數)
        訊息 = 參數["messages"]
        工具訊息 = [項 for 項 in 訊息 if 項["role"] == "tool"]
        if not 工具訊息:
            名稱, 工具參數 = "skills_list", {"category": self._工具參數標記}
        elif len(工具訊息) == 1:
            名稱, 工具參數 = "skill_view", {"name": "stable"}
        else:
            return 模型回應快照(
                json.dumps({"answer": self._回應標記}, separators=(",", ":")),
                "stop", {"total_tokens": 3}, [],
            )
        呼叫 = {
            "id": f"call-{len(工具訊息) + 1}", "type": "function",
            "function": {"name": 名稱, "arguments": json.dumps(工具參數, separators=(",", ":"))},
        }
        return 模型回應快照("", "tool_calls", {}, [呼叫])


def _找Published資源(app) -> 生產Published執行資源:
    return next(資源 for 資源 in app.state.發布介面資源 if type(資源) is 生產Published執行資源)


def _接線(資源: 生產Published執行資源):
    編排器 = 資源._編排器
    呼叫庫 = 編排器._呼叫儲存庫
    協調器 = 呼叫庫._敏感交易協調器
    writer = 協調器._交易寫入器
    ledger = 編排器._開始執行嘗試.__self__
    runtime = 編排器._執行嘗試
    assert runtime._工具呼叫紀錄器.__self__ is 呼叫庫
    return 呼叫庫, 協調器, writer, ledger, runtime


def test_canonical_lifespan唯一敏感依賴identity且restart更換世代(tmp_path: Path, monkeypatch):
    web, db, bundles, skills = (
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
        tmp_path / "bundles", tmp_path / "skills",
    )
    身分 = 建立正式v1(web=web, db=db, bundles=bundles, skill_root=skills)
    _設定環境(monkeypatch, web, db, bundles, 建立ProductionDist(tmp_path))
    app = root_asgi.建立應用程式()

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        舊資源 = _找Published資源(app)
        舊接線 = _接線(舊資源)
        呼叫庫, 協調器, writer, ledger, _runtime = 舊接線
        assert type(呼叫庫) is SQLite呼叫儲存庫
        assert type(協調器) is 呼叫敏感交易協調器
        assert type(writer) is SQLite敏感稽核儲存庫
        assert type(ledger) is InvocationLedger橋接 and ledger._儲存庫 is 呼叫庫
        assert 呼叫庫._資料庫 == writer._資料庫 == db

    assert 舊資源._編排器 is None and 舊資源._代理._編排器 is None
    assert all(
        not isinstance(值, sqlite3.Connection)
        for 物件 in 舊接線 for 值 in getattr(物件, "__dict__", {}).values()
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        新資源 = _找Published資源(app)
        新接線 = _接線(新資源)
        assert all(新 is not 舊 for 新, 舊 in zip(新接線, 舊接線))
        偵測次數 = [0]
        當前協調器 = 新接線[1]
        原偵測 = 當前協調器.偵測呼叫

        def 計數偵測(*args, **kwargs):
            偵測次數[0] += 1
            return 原偵測(*args, **kwargs)

        monkeypatch.setattr(當前協調器, "偵測呼叫", 計數偵測)
        miss = client.post(
            "/v1/endpoints/not-present/invoke",
            headers={"Authorization": f"Bearer {身分['key']}"},
            json={"input": {"payload": "detector-must-not-read"}},
        )
        assert miss.status_code == 404 and 偵測次數 == [0]


def test_canonical_route五目標warning與同DB_restart均可讀(tmp_path: Path, monkeypatch):
    input標記, metadata標記, response標記, arguments標記, result標記 = _安全標記們()
    web, db, bundles, skills = (
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
        tmp_path / "bundles", tmp_path / "skills",
    )
    身分 = 建立正式v1(
        web=web, db=db, bundles=bundles, skill_root=skills,
        skill_body="BUNDLE-V1 " + result標記,
    )
    _設定環境(monkeypatch, web, db, bundles, 建立ProductionDist(tmp_path))
    模型 = _五目標模型(arguments標記, response標記)
    monkeypatch.setattr(GeminiADC供應商, "產生發布回應", lambda self, **kw: 模型.產生(**kw))
    app = root_asgi.建立應用程式()
    輸入 = {"contact": input標記}
    metadata = {"phone": metadata標記}
    正規輸入 = json.dumps(輸入, sort_keys=True, separators=(",", ":"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/endpoints/stable/invoke",
            headers={"Authorization": f"Bearer {身分['key']}"},
            json={"input": 輸入, "metadata": metadata},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["warnings"] == [{
            "code": "sensitive_data_detected", "message": "回應包含可能的敏感資料。",
        }]
        assert set(body["warnings"][0]) == {"code", "message"}
        invocation_id = body["invocation"]["id"]

    assert 模型.calls[0]["messages"][-1]["content"] == 正規輸入
    assert 模型.calls[0]["messages"][-1]["metadata"]["input_json"] == 輸入
    assert all(call["response_schema"] == 模型.calls[0]["response_schema"] for call in 模型.calls)

    with sqlite3.connect(db) as connection:
        invocation = connection.execute(
            "SELECT status,input_json,metadata_json,output_json FROM endpoint_invocations WHERE id=?",
            (invocation_id,),
        ).fetchone()
        assert invocation == (
            "succeeded", 正規輸入,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            json.dumps({"answer": response標記}, sort_keys=True, separators=(",", ":")),
        )
        tools = connection.execute(
            "SELECT tool_name,arguments_json,result_json FROM endpoint_tool_calls "
            "WHERE invocation_id=? ORDER BY sequence_number",
            (invocation_id,),
        ).fetchall()
        assert len(tools) == 2
        assert tools[0] == (
            "skills_list", json.dumps({"category": arguments標記}, sort_keys=True, separators=(",", ":")),
            json.dumps({"success": True, "result": {"skills": []}}, sort_keys=True, separators=(",", ":")),
        )
        assert tools[1][0] == "skill_view"
        assert tools[1][1] == '{"name":"stable"}'
        assert json.loads(tools[1][2])["result"]["content"].endswith(result標記)
        targets = connection.execute(
            "SELECT target_type FROM invocation_sensitive_hits WHERE invocation_id=? ORDER BY target_type",
            (invocation_id,),
        ).fetchall()
        assert targets == [(name,) for name in sorted(
            ("input", "metadata", "response_data", "tool_arguments", "tool_result")
        )]
        hit_count = len(targets)
        audit_count = connection.execute(
            "SELECT count(*) FROM audit_events WHERE invocation_id=? AND action='published_api.sensitive_data_detected'",
            (invocation_id,),
        ).fetchone()[0]
        warning_snapshot = connection.execute(
            "SELECT payload_json FROM run_events WHERE invocation_id=? ORDER BY sequence_number DESC LIMIT 1",
            (invocation_id,),
        ).fetchone()[0]
        assert audit_count == hit_count == 5
        assert json.loads(warning_snapshot)["warnings"] == body["warnings"]

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/healthz").status_code == 200
        with sqlite3.connect(db) as connection:
            assert connection.execute(
                "SELECT status FROM endpoint_invocations WHERE id=?", (invocation_id,),
            ).fetchone() == ("succeeded",)
            assert connection.execute(
                "SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id=?", (invocation_id,),
            ).fetchone()[0] == hit_count
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE invocation_id=? AND action='published_api.sensitive_data_detected'",
                (invocation_id,),
            ).fetchone()[0] == audit_count
            assert connection.execute(
                "SELECT payload_json FROM run_events WHERE invocation_id=? ORDER BY sequence_number DESC LIMIT 1",
                (invocation_id,),
            ).fetchone()[0] == warning_snapshot


def test_startup敏感writer構造與DB_schema失敗均不安裝半套服務(tmp_path: Path, monkeypatch):
    web, db, bundles, skills = (
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
        tmp_path / "bundles", tmp_path / "skills",
    )
    建立正式v1(web=web, db=db, bundles=bundles, skill_root=skills)
    _設定環境(monkeypatch, web, db, bundles, 建立ProductionDist(tmp_path))
    app = root_asgi.建立應用程式()

    def 構造失敗(*_args, **_kwargs):
        raise RuntimeError("writer construction rejected")

    monkeypatch.setattr(執行模組, "SQLite敏感稽核儲存庫", 構造失敗)
    try:
        with TestClient(app):
            raise AssertionError
    except RuntimeError as error:
        assert str(error) == "發布介面啟動失敗"
    assert not hasattr(app.state, "發布介面資源")

    monkeypatch.setattr(執行模組, "SQLite敏感稽核儲存庫", SQLite敏感稽核儲存庫)
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE invocation_sensitive_hits")
        connection.execute("CREATE TABLE invocation_sensitive_hits(id TEXT PRIMARY KEY)")
    broken_app = root_asgi.建立應用程式()
    try:
        with TestClient(broken_app):
            raise AssertionError
    except RuntimeError as error:
        assert str(error) == "發布介面啟動失敗"
    assert not hasattr(broken_app.state, "發布介面資源")


def test_canonical_route_writer失敗fail_closed且不留部分invocation(tmp_path: Path, monkeypatch):
    input標記, _, _, _, _ = _安全標記們()
    web, db, bundles, skills = (
        tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
        tmp_path / "bundles", tmp_path / "skills",
    )
    身分 = 建立正式v1(web=web, db=db, bundles=bundles, skill_root=skills)
    _設定環境(monkeypatch, web, db, bundles, 建立ProductionDist(tmp_path))
    app = root_asgi.建立應用程式()
    with TestClient(app, raise_server_exceptions=False) as client:
        writer = _接線(_找Published資源(app))[2]

        def 寫入失敗(*_args, **_kwargs):
            raise RuntimeError("writer rejected")

        monkeypatch.setattr(writer, "寫入呼叫交易", 寫入失敗)
        response = client.post(
            "/v1/endpoints/stable/invoke",
            headers={"Authorization": f"Bearer {身分['key']}"},
            json={"input": {"contact": input標記}},
        )
        assert response.status_code == 500
        assert response.json()["error"] == {
            "code": "internal_error", "message": "伺服器內部錯誤。", "details": {},
        }
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM endpoint_invocations").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (0,)
