"""C1-A：PostgreSQL publication graph 的單交易與 ACK-loss 因果契約。"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from 繁中代理.發布介面.技能套件.CloudStorage權威 import CloudStorage套件發布收據

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面 import PostgreSQL端點庫 as endpoint_module
from 繁中代理.發布介面 import PostgreSQL版本服務 as version_module
from 繁中代理.發布介面.規劃.端點發布 import 端點發布耐久性未知
from 繁中代理.發布介面.規劃.版本服務 import 版本配置提交判定


def _settings():
    return 交易儲存設定("postgres", "postgresql:///app?host=/cloudsql/p:r:i", "p:r:i", 0, 1, 1)


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class StrictConnection:
    """只接受 PostgreSQL SQL，且由完整 statement pattern 提供 dict_row。"""
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def execute(self, sql, params=()):
        from pglast import parse_sql
        parse_sql(sql.replace("%s", "NULL"))
        assert sql.count("%s") == len(params)
        self.calls.append((sql, params))
        # advisory lock is a command-shaped SELECT and has no row projection.
        if "pg_advisory_xact_lock" not in sql and (sql.lstrip().upper().startswith("SELECT") or " RETURNING " in sql.upper()):
            return Cursor(self.rows.pop(0) if self.rows else None)
        return Cursor()


class Unit:
    def __init__(self, *connections, ack_loss=False):
        self.connections = list(connections)
        self.ack_loss = ack_loss
        self.transactions = 0

    @contextmanager
    def 交易(self):
        conn = self.connections[self.transactions]
        self.transactions += 1
        yield conn
        if self.ack_loss and self.transactions == 1:
            raise OSError("commit acknowledgement lost")


def _endpoint_inputs(monkeypatch):
    draft = SimpleNamespace(草稿識別碼="draft-1")
    snapshot = SimpleNamespace(
        original_requirement_text="r", system_prompt="p", allowed_skills=[], allowed_tools=[],
        tool_schema_snapshot={}, tool_runtime_revision="runtime-1", model_config_snapshot={},
        retry_policy={}, skill_bundle_manifest={}, input_schema=None, response_schema={},
        created_by_user_id="owner",
    )
    credential = SimpleNamespace(
        name="key", purpose="test", key_version=1, key_nonce=b"n" * 12,
        key_ciphertext=b"cipher", key_hash="a" * 64, key_prefix="pk_preview",
        key_last4="1234", expires_at=99.0, ip_allowlist=[], rate_limit_requests=10,
        created_by_user_id="owner",
    )
    confirm = SimpleNamespace(slug="demo", endpoint_limit=60, window_seconds=60)
    receipt = SimpleNamespace(
        套件識別碼="bundle-1", 清單參照="bundles/bundle-1/manifest.json",
        清單摘要="b" * 64, 套件雜湊="c" * 64, 總位元組數=7,
    )
    ids = ("ep-1", "v-1", "cred-1", "sa-1", "bundle-1", "audit-1", 10.0)
    monkeypatch.setattr(endpoint_module, "_發布前驗證", lambda *_: (draft, snapshot, credential, confirm))
    monkeypatch.setattr(endpoint_module, "_驗證預配識別", lambda _: ids)
    monkeypatch.setattr(endpoint_module, "_驗證CloudStorage預配關係", lambda *_: receipt)
    return draft, snapshot, credential, receipt, ids


def test_initial_prepared_graph_is_one_transaction_authority_before_insert_and_all_edges(monkeypatch):
    draft, snapshot, credential, receipt, _ = _endpoint_inputs(monkeypatch)
    conn = StrictConnection(rows=[None, {"current_version_id": "v-1"}])
    unit = Unit(conn)
    service = endpoint_module.PostgreSQL端點庫(_settings(), *(lambda: "unused" for _ in range(4)), lambda: 1.0)
    service._工作單元 = unit
    marker = []
    def authority():
        assert not any(sql.lstrip().upper().startswith("INSERT") for sql, _ in conn.calls)
        assert any("FOR UPDATE" in sql for sql, _ in conn.calls)
        marker.append(True)
        return object()
    result = service.發布已準備圖形(
        "owner", draft, snapshot, credential, object(), receipt,
        請求識別碼="request-1", 寫入前權威確認=authority,
    )
    sql = "\n".join(statement for statement, _ in conn.calls)
    assert unit.transactions == 1 and marker == [True]
    assert result.endpoint_id == "ep-1"
    for table in ("service_accounts", "published_endpoints", "published_endpoint_versions",
                  "published_draft_consumptions", "published_endpoint_version_metadata",
                  "endpoint_credentials", "published_skill_bundles", "audit_events"):
        assert f"INSERT INTO {table}" in sql
    assert "current_version_id IS NULL" in sql and "RETURNING current_version_id" in sql
    audit = next(params for statement, params in conn.calls if statement.startswith("INSERT INTO audit_events"))
    assert "request-1" in audit


def test_initial_graph_accepts_only_exact_generation_pinned_cloud_receipt():
    ids = ("ep-1", "v-1", "cred-1", "sa-1", "bundle-1", "audit-1", 10.0)
    reference = "bundles/v1/bundle-1/manifest.json#generation=7"
    digest, bundle_hash = "d" * 64, "e" * 64
    snapshot = SimpleNamespace(skill_bundle_manifest={
        "bundle_id": "bundle-1", "manifest_reference": reference,
        "manifest_digest": digest, "sha256": bundle_hash,
    })
    receipt = CloudStorage套件發布收據(
        "bundle-1", reference, digest, bundle_hash, 8, "bucket",
        "bundles/v1/bundle-1/manifest.json", 7,
    )
    rebuilt = endpoint_module._驗證CloudStorage預配關係(ids, snapshot, receipt, "request-1")
    assert rebuilt == receipt and rebuilt is not receipt
    with pytest.raises(endpoint_module.端點發布輸入錯誤):
        endpoint_module._驗證CloudStorage預配關係(ids, snapshot, SimpleNamespace(), "request-1")


def test_initial_commit_ack_loss_uses_fresh_complete_graph_readback(monkeypatch):
    draft, snapshot, credential, receipt, _ = _endpoint_inputs(monkeypatch)
    writer = StrictConnection(rows=[None, {"current_version_id": "v-1"}])
    complete = {"graph_matches": True, "any_candidate": True}
    reader = StrictConnection(rows=[complete])
    service = endpoint_module.PostgreSQL端點庫(_settings(), *(lambda: "unused" for _ in range(4)), lambda: 1.0)
    service._工作單元 = Unit(writer, reader, ack_loss=True)
    result = service.發布已準備圖形("owner", draft, snapshot, credential, object(), receipt, 請求識別碼="request-1")
    assert result.version_id == "v-1" and service._工作單元.transactions == 2
    read_sql = reader.calls[0][0]
    for table in ("service_accounts", "published_endpoints", "published_endpoint_versions",
                  "published_draft_consumptions", "published_endpoint_version_metadata",
                  "endpoint_credentials", "published_skill_bundles", "audit_events"):
        assert table in read_sql


def test_configure_activate_one_transaction_and_ack_readback_is_three_state(monkeypatch):
    snapshot = version_module.發布版本快照(
        "r", "p", [], [], {}, "runtime-1", {}, {}, {}, None, {}, "owner",
    )
    receipt = SimpleNamespace(套件識別碼="bundle-2", 清單參照="b/manifest.json", 清單摘要="d"*64,
                              套件雜湊="e"*64, 總位元組數=8)
    monkeypatch.setattr(version_module, "_重建版本快照", lambda _: snapshot)
    monkeypatch.setattr(version_module, "_重建原子套件收據", lambda *_: receipt)
    writer = StrictConnection(rows=[{"owner_user_id": "owner", "status": "active", "current_version_id": "v-1"},
                                           {"version_number": 1, "input_schema": None, "response_schema": {}},
                                           {"current_version_id": "v-2"}])
    expected = {"graph_matches": True, "any_candidate": True}
    reader = StrictConnection(rows=[expected])
    service = version_module.PostgreSQL版本配置服務(_settings(), lambda: "unused", lambda: 1.0)
    service._工作單元 = Unit(writer, reader, ack_loss=True)
    result = service.配置並啟用(
        執行者使用者識別碼="owner", 執行者類型="user", 端點識別碼="ep-1",
        已準備快照=snapshot, 已準備版本識別碼="v-2", 已準備時間=20.0,
        套件收據=receipt, 稽核識別碼="audit-2", 請求識別碼=None,
        套件驗證器=lambda *_: True,
    )
    assert result.new_version_id == "v-2" and service._工作單元.transactions == 2
    sql = "\n".join(statement for statement, _ in writer.calls)
    assert "FOR UPDATE" in sql and "INSERT INTO published_endpoint_versions" in sql
    assert "INSERT INTO published_skill_bundles" in sql and "INSERT INTO audit_events" in sql
    assert "RETURNING current_version_id" in sql
    probe = StrictConnection(rows=[expected])
    service._工作單元 = Unit(probe)
    assert service.判定版本配置提交結果(
        執行者使用者識別碼="owner", 執行者類型="user", 端點識別碼="ep-1",
        版本識別碼="v-2", 版本號碼=2, 套件收據=receipt,
        稽核識別碼="audit-2", 建立時間=20.0,
    ) is 版本配置提交判定.已提交


def test_modified_adapters_do_not_fallback_to_tuple_or_rowcount_guessing():
    # dict_row is the only row contract; DML rowcount is allowed for ordinary
    # invariants, but ACK outcome must be based on fresh exact projections.
    assert "isinstance(row, (tuple, list))" not in Path(version_module.__file__).read_text(encoding="utf-8")
    assert "rowcount" not in Path(endpoint_module.__file__).read_text(encoding="utf-8").split("def _判定初始提交", 1)[1]
    assert "rowcount" not in Path(version_module.__file__).read_text(encoding="utf-8").split("def _判定配置並啟用提交", 1)[1]
