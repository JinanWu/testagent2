"""A18-04 canonical Admin完整紀錄後端產品驗收。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


_管理員帳號 = "a18-admin"
_擁有者甲帳號 = "a18-owner-a"
_擁有者乙帳號 = "a18-owner-b"
_密碼 = "correct horse"
_端點甲, _版本甲, _呼叫甲 = "endpoint-a18-a", "version-a18-a", "invocation-a18-a"
_端點乙, _版本乙, _呼叫乙 = "endpoint-a18-b", "version-a18-b", "invocation-a18-b"
_主列標記 = "A18_INPUT_MARKER"
_事件標記 = "A18_EVENT_MARKER"
_工具標記 = "A18_ARGUMENT_MARKER"
_所有標記 = (_主列標記, _事件標記, _工具標記)


@dataclass(frozen=True)
class _Canonical環境:
    """保存可從同一持久狀態重建canonical app的最小authority。"""

    建立應用: Callable[[], FastAPI]
    管理員識別碼: str
    擁有者甲識別碼: str
    擁有者乙識別碼: str
    資料庫路徑: Path


def _建立canonical環境(暫存目錄: Path) -> _Canonical環境:
    """建立真Web／Published SQLite設定，不在app construction做I/O。"""
    暫存目錄 = 暫存目錄.resolve()
    套件根 = 暫存目錄 / "bundles"
    套件根.mkdir()
    使用者 = 使用者庫(暫存目錄 / "web.sqlite3")
    try:
        管理員 = 使用者.建立使用者(_管理員帳號, _密碼, roles=["admin"])
        擁有者甲 = 使用者.建立使用者(_擁有者甲帳號, _密碼, roles=["user"])
        擁有者乙 = 使用者.建立使用者(_擁有者乙帳號, _密碼, roles=["user"])
    finally:
        使用者.連線.close()
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=60,
    )
    Published設定 = Published生產設定(
        暫存目錄 / "published.sqlite3", 套件根,
        lambda _工具庫: None, lambda: {"fake": object()},
    )
    return _Canonical環境(
        建立應用=lambda: 建立CP4ASGI應用程式(網頁設定, Published設定),
        管理員識別碼=str(管理員["id"]),
        擁有者甲識別碼=str(擁有者甲["id"]),
        擁有者乙識別碼=str(擁有者乙["id"]),
        資料庫路徑=Published設定.發布資料庫路徑,
    )


def _種入呼叫(環境: _Canonical環境) -> None:
    """經獨立連線提交兩位Owner的合法endpoint/invocation graph。"""
    with sqlite3.connect(環境.資料庫路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 服務, 端點, 版本, 呼叫, 擁有者, 後綴 in (
            ("service-a18-a", _端點甲, _版本甲, _呼叫甲, 環境.擁有者甲識別碼, "a"),
            ("service-a18-b", _端點乙, _版本乙, _呼叫乙, 環境.擁有者乙識別碼, "b"),
        ):
            連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (服務, 1.0))
            連線.execute(
                "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
                "current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)",
                (端點, 擁有者, 服務, f"a18-endpoint-{後綴}", "active", 1.0, 1.0),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (版本, 端點, 1, "requirement", "system", "[]", "[]", "{}", "revision",
                 "{}", "{}", "{}", None, "{}", 0, 擁有者, 1.0),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (版本, 端點))
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,credential_id,"
                "request_id,session_id,message_id,status,input_json,metadata_json,output_json,error_json,"
                "usage_json,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,created_at,completed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (呼叫, 端點, 版本, None, f"request-a18-{後綴}", f"session-a18-{後綴}",
                 f"message-a18-{後綴}", "succeeded", json.dumps({"prompt": f"input-{後綴}"}),
                 json.dumps({"trace": f"metadata-{後綴}"}), json.dumps({"answer": f"output-{後綴}"}),
                 None, json.dumps({"total_tokens": 2}), 22, {
                     "a": "2bdccfc57f310655fe9362c61ff6d6cc3b28b9279be1b1abd025a81d8557fe7e",
                     "b": "d11680c21b3289363f0ed9050ad6bd7bdf821bc46bc63240ef46a83d141a96ee",
                 }[後綴], 2.0, "pricing-v1", 10.0, 12.0),
            )
        連線.execute("UPDATE endpoint_invocations SET input_json=? WHERE id=?",
                     (json.dumps({"prompt": _主列標記}), _呼叫甲))
        連線.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
            ("event-a18", _呼叫甲, 1, "model.completed", json.dumps({"phase": _事件標記}), 11.0),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,"
            "arguments_json,outcome,result_json,error_json,latency_ms,retry_of_tool_call_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tool-a18", _呼叫甲, "event-a18", 1, "lookup", json.dumps({"query": _工具標記}),
             "success", json.dumps({"count": 1}), None, 1.0, None, 11.5),
        )


def _登入管理員(客戶端: TestClient) -> None:
    """以真帳密交換canonical Web session cookie。"""
    回應 = 客戶端.post("/api/auth/login", json={"username": _管理員帳號, "password": _密碼})
    assert 回應.status_code == 200


def _詳情路徑(端點: str = _端點甲, 呼叫: str = _呼叫甲) -> str:
    """建立canonical Admin detail路徑。"""
    return f"/api/admin/endpoints/{端點}/invocations/{呼叫}"


def _detail稽核(資料庫路徑: Path) -> list[tuple]:
    """以獨立connection讀取已提交且不含raw的detail audits。"""
    with sqlite3.connect(資料庫路徑) as 連線:
        return 連線.execute(
            "SELECT rowid,action,outcome,actor_type,actor_id,endpoint_id,invocation_id,metadata_json "
            "FROM audit_events WHERE action='audit.detail.view' ORDER BY rowid"
        ).fetchall()


def test_A18_canonical_Admin真實登入_list_detail且稽核已提交(tmp_path):
    """真session經canonical兩條GET讀取資料，detail成功時audit已由獨立連線看見。"""
    環境 = _建立canonical環境(tmp_path)
    with TestClient(環境.建立應用(), raise_server_exceptions=False) as 客戶端:
        _種入呼叫(環境)
        _登入管理員(客戶端)
        列表 = 客戶端.get(f"/api/admin/endpoints/{_端點甲}/invocations")
        assert 列表.status_code == 200
        assert 列表.json()["items"] == [{
            "invocation_id": _呼叫甲, "endpoint_id": _端點甲, "endpoint_version_id": _版本甲,
            "request_id": "request-a18-a", "status": "succeeded", "error_code": None,
            "latency_ms": 2.0, "created_at": 10.0, "completed_at": 12.0, "has_redactions": False,
        }]
        assert not any(標記 in 列表.text for 標記 in _所有標記)
        詳情 = 客戶端.get(_詳情路徑())
        assert 詳情.status_code == 200
        assert 詳情.json()["input"] == {"prompt": _主列標記}
        assert 詳情.json()["run_events"][0]["payload"] == {"phase": _事件標記}
        assert 詳情.json()["tool_calls"][0]["arguments"] == {"query": _工具標記}
    assert _detail稽核(環境.資料庫路徑) == [
        (1, "audit.detail.view", "success", "user", 環境.管理員識別碼, _端點甲, _呼叫甲, "{}")
    ]


def test_A18_Owner_API_key_未登入與偽造header皆零raw且anti_enumeration一致(tmp_path):
    """未登入零audit；真非Admin先提交denied audit且零raw；錯配與missing同一404。"""
    環境 = _建立canonical環境(tmp_path)
    with TestClient(環境.建立應用(), raise_server_exceptions=False) as 客戶端:
        _種入呼叫(環境)
        憑證 = SQLite憑證儲存庫(
            環境.資料庫路徑, AESGCM憑證封套({1: b"k" * 32}, 1),
            clock=lambda: 100.0, id_factory=lambda: "credential-a18",
        ).建立(_端點甲, WebOwnerPrincipal(環境.擁有者甲識別碼), name="e2e",
               purpose="A18 canonical E2E", expires_at=200.0, rate_limit_requests=60)
        未登入 = 客戶端.get(_詳情路徑(), headers={
            "X-Admin": "true", "X-User-Id": 環境.管理員識別碼,
            "Authorization": f"Bearer {憑證.api_key}",
        })
        assert 未登入.status_code == 401
        assert not any(標記 in 未登入.text for 標記 in _所有標記)
        for 帳號 in (_擁有者甲帳號, _擁有者乙帳號):
            assert 客戶端.post("/api/auth/login", json={"username": 帳號, "password": _密碼}).status_code == 200
            拒絕 = 客戶端.get(_詳情路徑(), headers={
                "X-Admin": "true", "X-User-Id": 環境.管理員識別碼,
                "Authorization": f"Bearer {憑證.api_key}",
            })
            assert 拒絕.status_code == 403
            assert not any(標記 in 拒絕.text for 標記 in _所有標記)
            客戶端.cookies.clear()
        assert [(列[1], 列[2], 列[4], 列[5], 列[6], 列[7])
                for 列 in _detail稽核(環境.資料庫路徑)] == [
            ("audit.detail.view", "denied", 環境.擁有者甲識別碼, None, None, "{}"),
            ("audit.detail.view", "denied", 環境.擁有者乙識別碼, None, None, "{}"),
        ]
        _登入管理員(客戶端)
        missing = 客戶端.get(_詳情路徑(呼叫="missing-a18"))
        wrong = 客戶端.get(_詳情路徑(呼叫=_呼叫乙))
        assert missing.status_code == wrong.status_code == 404
        assert missing.content == wrong.content
        assert not any(標記 in missing.text + wrong.text for 標記 in _所有標記)
    稽核 = _detail稽核(環境.資料庫路徑)
    assert [(列[1], 列[2], 列[4], 列[5], 列[6], 列[7]) for 列 in 稽核] == [
        ("audit.detail.view", "denied", 環境.擁有者甲識別碼, None, None, "{}"),
        ("audit.detail.view", "denied", 環境.擁有者乙識別碼, None, None, "{}"),
        ("audit.detail.view", "success", 環境.管理員識別碼, None, None, "{}"),
        ("audit.detail.view", "success", 環境.管理員識別碼, None, None, "{}"),
    ]


def test_A18_正式遮蔽後主列事件工具只回墓碑且稽核零原文(tmp_path):
    """使用正式redaction transaction後，所有層級均不可恢復原文。"""
    環境 = _建立canonical環境(tmp_path)
    with TestClient(環境.建立應用(), raise_server_exceptions=False) as 客戶端:
        _種入呼叫(環境)
        服務 = SQLite不可逆遮蔽服務(str(環境.資料庫路徑))
        for 索引, (類型, 列ID) in enumerate((
            ("invocation_input", _呼叫甲), ("run_event", "event-a18"),
            ("tool_arguments", "tool-a18"),
        ), 1):
            服務.redact(
                True, f"redaction-a18-{索引}", f"audit-redaction-a18-{索引}",
                環境.管理員識別碼, f"request-redaction-a18-{索引}", _呼叫甲,
                類型, 列ID, "", "privacy request", 20.0 + 索引,
            )
        _登入管理員(客戶端)
        列表 = 客戶端.get(f"/api/admin/endpoints/{_端點甲}/invocations")
        assert 列表.status_code == 200 and 列表.json()["items"][0]["has_redactions"] is True
        詳情 = 客戶端.get(_詳情路徑())
        assert 詳情.status_code == 200
        assert "$tombstone" in 詳情.json()["input"]
        assert "$tombstone" in 詳情.json()["run_events"][0]["payload"]
        assert "$tombstone" in 詳情.json()["tool_calls"][0]["arguments"]
        assert len(詳情.json()["redactions"]) == 3
        assert all(set(項) == {
            "id", "target_type", "target_row_id", "json_path", "original_sha256",
            "reason", "actor", "audit_event_id", "is_tombstone", "redacted_at",
        } for 項 in 詳情.json()["redactions"])
        assert all(項["actor"] == {"type": "admin", "id": 環境.管理員識別碼}
                   for 項 in 詳情.json()["redactions"])
        assert not any(標記 in 詳情.text for 標記 in _所有標記)
    with sqlite3.connect(環境.資料庫路徑) as 連線:
        安全持久資料 = repr(連線.execute(
            "SELECT action,outcome,metadata_json FROM audit_events ORDER BY rowid"
        ).fetchall()) + repr(連線.execute(
            "SELECT target_type,target_row_id,json_path,reason FROM endpoint_redactions ORDER BY rowid"
        ).fetchall())
    assert not any(標記 in 安全持久資料 for 標記 in _所有標記)


def test_A18_restart重讀detail且audit_append_only順序不變(tmp_path):
    """關閉第一app後重建新app，持久detail可讀且既有audit不被改寫。"""
    環境 = _建立canonical環境(tmp_path)
    第一應用 = 環境.建立應用()
    with TestClient(第一應用, raise_server_exceptions=False) as 客戶端:
        _種入呼叫(環境)
        _登入管理員(客戶端)
        assert 客戶端.get(_詳情路徑()).status_code == 200
    第一次 = _detail稽核(環境.資料庫路徑)
    第二應用 = 環境.建立應用()
    assert 第二應用 is not 第一應用
    with TestClient(第二應用, raise_server_exceptions=False) as 客戶端:
        _登入管理員(客戶端)
        詳情 = 客戶端.get(_詳情路徑())
        assert 詳情.status_code == 200 and 詳情.json()["invocation"]["id"] == _呼叫甲
    第二次 = _detail稽核(環境.資料庫路徑)
    assert 第二次[:1] == 第一次
    assert len(第二次) == 2 and 第二次[1][0] > 第二次[0][0]


@pytest.mark.parametrize(
    "payload",
    ['{"duplicate":1,"duplicate":2}', '"' + "x" * 1_048_577 + '"'],
    ids=("duplicate-key", "oversize"),
)
def test_A18_corruption與oversize經canonical_HTTP固定500且無partial_raw(tmp_path, payload):
    """動態JSON損壞或超量時，canonical detail固定失敗且不回任何partial payload。"""
    環境 = _建立canonical環境(tmp_path)
    with TestClient(環境.建立應用(), raise_server_exceptions=False) as 客戶端:
        _種入呼叫(環境)
        with sqlite3.connect(環境.資料庫路徑) as 連線:
            連線.execute("UPDATE endpoint_invocations SET input_json=? WHERE id=?", (payload, _呼叫甲))
        _登入管理員(客戶端)
        回應 = 客戶端.get(_詳情路徑())
        assert 回應.status_code == 500
        assert 回應.json() == {"detail": "呼叫紀錄不可取得"}
        assert not any(標記 in 回應.text for 標記 in _所有標記)


def test_A18_canonical_OpenAPI只有兩條Admin_GET且無export(tmp_path):
    """Live canonical schema不得擴張為export/download/raw search或寫入操作。"""
    環境 = _建立canonical環境(tmp_path)
    with TestClient(環境.建立應用(), raise_server_exceptions=False) as 客戶端:
        paths = 客戶端.get("/openapi.json").json()["paths"]
    admin = {路徑: 定義 for 路徑, 定義 in paths.items() if 路徑.startswith("/api/admin/")}
    assert set(admin) == {
        "/api/admin/endpoints/{endpoint_id}/invocations",
        "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}",
    }
    assert all(tuple(定義) == ("get",) for 定義 in admin.values())
    assert not any(禁止 in str(admin).lower() for 禁止 in ("export", "download", "raw_search"))
