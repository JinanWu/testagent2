"""A08-3 hermetic root-app fixture, model gate, SQL trace and corruption helpers."""
from __future__ import annotations
import base64

import copy
import json
import os
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from a08_3_formal_publish import 建立正式v1, 正式切換v2
from production_spa_support import 建立ProductionDist
from 繁中代理.模型供應商 import GeminiADC供應商
from 繁中代理.發布介面.資料庫結構契約 import 驗證資料庫結構
from 繁中代理.發布介面.執行期.呼叫橋接 import 發布執行嘗試橋接
from 繁中代理.發布介面.執行期.快照儲存庫 import SQLite發布快照儲存庫
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.規劃.版本服務 import SQLite目前版本解析器


七欄 = {"ok", "endpoint", "invocation", "data", "usage", "warnings", "error"}


class 可觀測Gemini:
    """First A call is invalid; block deterministically at retry entry."""
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.retry已進入 = threading.Event()
        self.retry可執行 = threading.Event()
        self.drain已進入 = threading.Event()
        self.drain可執行 = threading.Event()
        self._lock = threading.Lock()

    def 產生(self, **參數: Any) -> 模型回應快照:
        call = copy.deepcopy(參數)
        with self._lock:
            self.calls.append(call)
            ordinal = len(self.calls)
        prompt = call["messages"][0]["content"]
        version = "v2" if "SYSTEM-V2" in prompt else "v1"
        users = json.dumps([x.get("content") for x in call["messages"] if x.get("role") == "user"])
        if "central-race" in users and ordinal == 1:
            return 模型回應快照('{"wrong":"schema"}', "stop", {"total_tokens": 1}, [])
        if "central-race" in users and ordinal == 2:
            self.retry已進入.set()
            assert self.retry可執行.wait(10), "retry gate not released"
        if "restart-drain" in users:
            self.drain已進入.set()
            assert self.drain可執行.wait(10), "drain gate not released"
        return 模型回應快照(json.dumps({"answer": version}, separators=(",", ":")),
                           "stop", {"total_tokens": 1}, [])


def _設定環境(monkeypatch, web: Path, db: Path, bundles: Path, Dist根: Path) -> None:
    for name in tuple(os.environ):
        if name.startswith(("TESTAGENT2_", "AIAGENT_")):
            monkeypatch.delenv(name, raising=False)
    values = {
        "TESTAGENT2_DB_PATH": str(web), "TESTAGENT2_PUBLISHED_DB_PATH": str(db),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(bundles),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
        "TESTAGENT2_MODEL_NAME": "gemini-root", "AIAGENT_GCP_PROJECT": "test-project",
        "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({
            "1": base64.urlsafe_b64encode(b"A" * 32).rstrip(b"=").decode("ascii"),
        }, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": base64.urlsafe_b64encode(
            b"O" * 32
        ).rstrip(b"=").decode("ascii"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def live(tmp_path: Path, monkeypatch):
    web, db, bundles, skills = (tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
                                 tmp_path / "bundles", tmp_path / "skills")
    identity = 建立正式v1(web=web, db=db, bundles=bundles, skill_root=skills)
    Dist根 = 建立ProductionDist(tmp_path)
    _設定環境(monkeypatch, web, db, bundles, Dist根)
    model = 可觀測Gemini()
    monkeypatch.setattr(GeminiADC供應商, "產生發布回應", lambda self, **kw: model.產生(**kw))
    traces: list[list[tuple]] = []
    snapshot_traces: list[list[tuple]] = []
    materials: list[tuple[object, ...]] = []
    original = SQLite目前版本解析器.__init__
    original_snapshot_init = SQLite發布快照儲存庫.__init__
    original_materials = 發布執行嘗試橋接._建立執行材料

    def traced_init(self, database_path, connection_factory=sqlite3.connect):
        def factory(*args, **kwargs):
            evidence = []
            traces.append(evidence)
            connection = connection_factory(*args, **kwargs)
            安裝SQL觀測(connection, evidence)
            return connection
        original(self, database_path, factory)
    monkeypatch.setattr(SQLite目前版本解析器, "__init__", traced_init)

    def traced_snapshot_init(self, database_path, digest, connection_factory=sqlite3.connect):
        def factory(*args, **kwargs):
            evidence = []
            snapshot_traces.append(evidence)
            connection = connection_factory(*args, **kwargs)
            安裝SQL觀測(connection, evidence)
            return connection
        original_snapshot_init(self, database_path, digest, factory)
    monkeypatch.setattr(SQLite發布快照儲存庫, "__init__", traced_snapshot_init)

    def traced_materials(self, request):
        snapshot, payload = original_materials(self, request)
        tools = tuple((item.name, item.revision, item.digest) for item in snapshot.tool_snapshot)
        model = tuple(
            (name, object.__getattribute__(snapshot.model_config, name))
            for name in snapshot.model_config.__dataclass_fields__
        )
        materials.append((
            snapshot.endpoint_id, snapshot.service_account_id, snapshot.version_id,
            snapshot.system_prompt, snapshot.permission_snapshot_digest,
            snapshot.skill_bundle_hash, snapshot.manifest_reference,
            snapshot.tool_handler_release, tools, model,
        ))
        return snapshot, payload
    monkeypatch.setattr(發布執行嘗試橋接, "_建立執行材料", traced_materials)

    result = {**identity, "web": web, "db": db, "bundles": bundles, "skills": skills,
              "model": model, "traces": traces, "snapshot_traces": snapshot_traces,
              "materials": materials}

    def publish_v2():
        result["v2"] = 正式切換v2(web=web, db=db, bundles=bundles, skill_root=skills,
                                     owner=identity["owner"], endpoint=identity["endpoint"])
    result["publish_v2"] = publish_v2
    return result


def 呼叫(client: TestClient, live: dict[str, Any], payload: dict[str, object], slug="stable"):
    return client.post(f"/v1/endpoints/{slug}/invoke",
                       headers={"Authorization": f"Bearer {live['key']}"}, json={"input": payload})


def 安裝SQL觀測(connection: sqlite3.Connection, evidence: list) -> None:
    """由SQLite parser將每條statement綁定其實際table／column read事件。"""
    pending: list[tuple[str, str, str | None]] = []
    def authorize(action, table, column, _database, source):
        if action == sqlite3.SQLITE_READ:
            pending.append((table, column, source))
        return sqlite3.SQLITE_OK
    def traced(sql):
        evidence.append((sql, tuple(pending)))
        pending.clear()
    connection.set_authorizer(authorize)
    connection.set_trace_callback(traced)


def _authority_statements(evidence: list) -> list[tuple[str, tuple]]:
    tables = {"published_endpoints", "published_endpoint_versions"}
    return [item for item in evidence if any(read[0] in tables for read in item[1])]
def resolver_sql(evidence: list) -> str:
    """以SQLite解析後read事件證明每條request connection只有一個current authority statement。"""
    authority = _authority_statements(evidence)
    assert len(authority) == 1
    sql, reads = authority[0]
    required = {("published_endpoints", column) for column in ("slug", "status", "current_version_id")}
    required |= {("published_endpoint_versions", column) for column in ("id", "endpoint_id")}
    assert required <= {(table, column) for table, column, _source in reads}
    normalized = " ".join(sql.lower().split())
    assert "published_endpoints e join published_endpoint_versions v" in normalized
    assert "e.slug=" in normalized and "e.status='active'" in normalized
    assert "v.id=e.current_version_id" in normalized and "v.endpoint_id=e.id" in normalized
    return sql
def snapshot_select_sql(evidence: list) -> list[str]:
    """以SQLite解析後read事件拒絕snapshot connection的額外authority statement。"""
    authority = _authority_statements(evidence)
    assert len(authority) == 1
    sql, reads = authority[0]
    pairs = {(table, column) for table, column, _source in reads}
    assert ("published_endpoint_versions", "id") in pairs
    assert ("published_endpoints", "current_version_id") not in pairs
    assert ("published_endpoints", "slug") not in pairs
    return [sql]


def _竄改不可變列(connection: sqlite3.Connection, trigger: str, sql: str, values=()) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,),
    ).fetchone()
    assert row is not None and type(row[0]) is str
    connection.execute(f'DROP TRIGGER "{trigger}"')
    cursor = connection.execute(sql, values)
    assert cursor.rowcount == 1
    connection.execute(row[0])


def 破壞(db: Path, bundles: Path, version: str, case: str) -> object:
    if case == "manifest_digest":
        with sqlite3.connect(db) as c:
            row = c.execute("SELECT bundle_id FROM published_skill_bundles WHERE version_id=?", (version,)).fetchall()
            assert len(row) == 1
            bundle = row[0][0]
        manifest = bundles / bundle / "manifest.json"
        data = json.loads(manifest.read_text())
        data["created_by_user_id"] = "tampered"
        原模式 = stat.S_IMODE(manifest.stat().st_mode)
        manifest.chmod(原模式 | stat.S_IWUSR)
        try:
            manifest.write_text(json.dumps(data), encoding="utf-8")
        finally:
            manifest.chmod(原模式)
        return json.loads(manifest.read_text(encoding="utf-8"))["created_by_user_id"]
    with sqlite3.connect(db) as c:
        if case in {"bundle_hash", "bundle_size", "bundle_identity"}:
            column, value = {"bundle_hash": ("bundle_hash", "0" * 64),
                             "bundle_size": ("total_bytes", 1),
                             "bundle_identity": ("bundle_id", "wrong-bundle")}[case]
            _竄改不可變列(c, "published_skill_bundles_no_update",
                         f"UPDATE published_skill_bundles SET {column}=? WHERE version_id=?", (value, version))
            readback = c.execute(f"SELECT {column} FROM published_skill_bundles WHERE version_id=?",
                                 (version,)).fetchone()[0]
        elif case == "tool_release":
            _竄改不可變列(c, "published_endpoint_versions_no_update",
                         "UPDATE published_endpoint_versions SET tool_runtime_revision='wrong-release' WHERE id=?",
                         (version,))
            readback = c.execute("SELECT tool_runtime_revision FROM published_endpoint_versions WHERE id=?",
                                 (version,)).fetchone()[0]
        elif case in {"tool_revision", "tool_digest"}:
            data = json.loads(c.execute("SELECT tool_schema_snapshot_json FROM published_endpoint_versions WHERE id=?",
                                        (version,)).fetchone()[0])
            item = data["skills_list"]
            if case == "tool_revision": item["revision"] = "skills_list@wrong"
            else: item["description"] += " drift"
            encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
            _竄改不可變列(c, "published_endpoint_versions_no_update",
                         "UPDATE published_endpoint_versions SET tool_schema_snapshot_json=? WHERE id=?",
                         (encoded, version))
            readback = json.loads(c.execute("SELECT tool_schema_snapshot_json FROM published_endpoint_versions WHERE id=?",
                                            (version,)).fetchone()[0])["skills_list"]
        else:
            data = json.loads(c.execute("SELECT model_config_snapshot_json FROM published_endpoint_versions WHERE id=?",
                                        (version,)).fetchone()[0])
            if case == "model_provider": data["provider"] = "missing"
            else: data["schema_retry_count"] = 0
            encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
            _竄改不可變列(c, "published_endpoint_versions_no_update",
                         "UPDATE published_endpoint_versions SET model_config_snapshot_json=? WHERE id=?",
                         (encoded, version))
            readback = json.loads(c.execute("SELECT model_config_snapshot_json FROM published_endpoint_versions WHERE id=?",
                                            (version,)).fetchone()[0])
    with sqlite3.connect(db) as c:
        驗證資料庫結構(c)
    return readback
