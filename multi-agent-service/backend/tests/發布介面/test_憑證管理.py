import json
import sqlite3

import pytest

from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.憑證管理契約 import (
    找不到端點憑證錯誤, 憑證建立命令, 憑證撤銷收據,
    憑證管理操作錯誤, 憑證管理狀態,
    端點生命週期衝突錯誤,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


def _資料庫(path):
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES ('sa1',1),('sa2',1)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES ('ep1','owner-1','sa1','one','active',1,1),('ep2','owner-2','sa2','two','active',1,1)"
        )


def _新增(path, credential_id="cred-1", created_at=100.0, expires_at=40_000_000.0):
    return SQLite憑證儲存庫(
        path, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: created_at,
        id_factory=lambda: credential_id,
    ).建立(
        "ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose",
        expires_at=expires_at, ip_allowlist=("192.0.2.0/24",), rate_limit_requests=70,
    )


def test_owner空清單與missing_foreign在同一owner_scope區分(tmp_path):
    db = tmp_path / "list.sqlite3"
    _資料庫(db)
    service = SQLite憑證管理服務(db, 時鐘=lambda: 150.0)
    assert service.列出憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-1").項目 == ()
    for endpoint, owner in (("missing", "owner-1"), ("ep1", "owner-2")):
        with pytest.raises(找不到端點憑證錯誤) as error:
            service.列出憑證(端點識別碼=endpoint, 擁有者使用者識別碼=owner)
        assert type(error.value) is 找不到端點憑證錯誤


def test_清單只發布安全欄位與canonical資料(tmp_path):
    db = tmp_path / "safe.sqlite3"
    _資料庫(db)
    created = _新增(db)
    result = SQLite憑證管理服務(db, 時鐘=lambda: 150.0).列出憑證(
        端點識別碼="ep1", 擁有者使用者識別碼="owner-1",
    )
    summary = result.項目[0]
    assert (summary.憑證識別碼, summary.狀態, summary.IP允許清單) == (
        "cred-1", 憑證管理狀態.有效, ("192.0.2.0/24",),
    )
    assert created.api_key not in repr(result)
    for forbidden in ("api_key", "key_hash", "key_nonce", "key_ciphertext", "key_version", "revision"):
        assert not hasattr(summary, forbidden)


@pytest.mark.parametrize("offset,expected", [
    (15_552_000 - 1, 憑證管理狀態.有效),
    (15_552_000, 憑證管理狀態.閒置),
])
def test_清單inactive使用inclusive_boundary(tmp_path, offset, expected):
    db = tmp_path / f"inactive-{offset}.sqlite3"
    _資料庫(db)
    _新增(db)
    item = SQLite憑證管理服務(db, 時鐘=lambda: 100.0 + offset).列出憑證(
        端點識別碼="ep1", 擁有者使用者識別碼="owner-1",
    ).項目[0]
    assert item.狀態 is expected


def test_狀態優先序revoked高於expired高於inactive(tmp_path):
    db = tmp_path / "precedence.sqlite3"
    _資料庫(db)
    _新增(db, expires_at=200.0)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE endpoint_credentials SET revoked_at=150 WHERE id='cred-1'")
    item = SQLite憑證管理服務(db, 時鐘=lambda: 300.0).列出憑證(
        端點識別碼="ep1", 擁有者使用者識別碼="owner-1",
    ).項目[0]
    assert item.狀態 is 憑證管理狀態.已撤銷


@pytest.mark.parametrize("mutation", [
    "UPDATE endpoint_credentials SET last_used_at=999 WHERE id='cred-1'",
    "UPDATE endpoint_credentials SET created_at=600 WHERE id='cred-1'",
    "UPDATE endpoint_credentials SET expires_at=1e999 WHERE id='cred-1'",
    "UPDATE endpoint_credentials SET revoked_at=50 WHERE id='cred-1'",
    "UPDATE endpoint_credentials SET ip_allowlist_json='[\"192.0.2.1/24\"]' WHERE id='cred-1'",
    "UPDATE endpoint_credentials SET rate_limit_requests=x'3630' WHERE id='cred-1'",
])
def test_毀損row固定為操作錯誤(tmp_path, mutation):
    db = tmp_path / "corrupt.sqlite3"
    _資料庫(db)
    _新增(db)
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='endpoint_credentials'"
        ).fetchall()
        for (name,) in triggers:
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute(mutation)
    with pytest.raises(憑證管理操作錯誤) as error:
        SQLite憑證管理服務(db, 時鐘=lambda: 500.0).列出憑證(
            端點識別碼="ep1", 擁有者使用者識別碼="owner-1",
        )
    assert type(error.value) is 憑證管理操作錯誤 and error.value.__context__ is None


def test_管理建立區分不存在衝突並回authoritative一次性收據(tmp_path):
    db = tmp_path / "managed-create.sqlite3"
    _資料庫(db)
    vault = AESGCM憑證封套({1: b"m" * 32}, 1)
    service = SQLite憑證管理服務(db, vault, 時鐘=lambda: 100.0)
    command = 憑證建立命令("name", "purpose", 200.0, ("192.0.2.1",), 60)
    with pytest.raises(找不到端點憑證錯誤):
        service.建立憑證(端點識別碼="missing", 擁有者使用者識別碼="owner-1", 請求=command)
    with pytest.raises(找不到端點憑證錯誤):
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-2", 請求=command)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET status='disabled' WHERE id='ep1'")
    with pytest.raises(端點生命週期衝突錯誤):
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET status='active' WHERE id='ep1'")
    receipt = service.建立憑證(
        端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command,
    )
    assert receipt.建立時間 == 100.0 and receipt.狀態 is 憑證管理狀態.有效
    assert receipt.初始金鑰 not in repr(receipt)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT created_at FROM endpoint_credentials").fetchone() == (100.0,)


def test_管理撤銷owner與admin成功且already保留原時間(tmp_path):
    db = tmp_path / "managed-revoke.sqlite3"
    _資料庫(db)
    _新增(db, credential_id="cred-owner")
    _新增(db, credential_id="cred-admin")
    service = SQLite憑證管理服務(db, 時鐘=lambda: 150.0)
    owner = service.撤銷憑證(
        端點識別碼="ep1", 憑證識別碼="cred-owner", 擁有者使用者識別碼="owner-1",
        是否管理者=False, 請求識別碼="request-owner",
    )
    repeated = service.撤銷憑證(
        端點識別碼="ep1", 憑證識別碼="cred-owner", 擁有者使用者識別碼="owner-1",
        是否管理者=False, 請求識別碼="request-repeat",
    )
    admin = service.撤銷憑證(
        端點識別碼="ep1", 憑證識別碼="cred-admin", 擁有者使用者識別碼="admin-1",
        是否管理者=True, 請求識別碼="request-admin",
    )
    assert type(owner) is 憑證撤銷收據 and (owner.撤銷時間, owner.是否已撤銷) == (150.0, False)
    assert (repeated.撤銷時間, repeated.是否已撤銷) == (150.0, True)
    assert admin.是否已撤銷 is False
    with sqlite3.connect(db) as connection:
        requests = connection.execute("SELECT request_id FROM audit_events ORDER BY rowid").fetchall()
    assert requests == [("request-owner",), ("request-repeat",), ("request-admin",)]


def test_管理撤銷missing_wrong_endpoint_foreign皆exact_not_found(tmp_path):
    db = tmp_path / "managed-revoke-scope.sqlite3"
    _資料庫(db)
    _新增(db)
    service = SQLite憑證管理服務(db, 時鐘=lambda: 150.0)
    for endpoint, credential, owner in (
        ("ep1", "missing", "owner-1"),
        ("ep2", "cred-1", "owner-1"),
        ("ep1", "cred-1", "owner-2"),
    ):
        with pytest.raises(找不到端點憑證錯誤) as error:
            service.撤銷憑證(
                端點識別碼=endpoint, 憑證識別碼=credential,
                擁有者使用者識別碼=owner, 是否管理者=False,
                請求識別碼="request-denied",
            )
        assert type(error.value) is 找不到端點憑證錯誤 and error.value.__context__ is None
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (0,)
        assert connection.execute("SELECT revoked_at FROM endpoint_credentials").fetchone() == (None,)


@pytest.mark.parametrize("request_id", ["", " bad", "x\nleak"])
def test_管理撤銷audit請求識別碼驗證失敗會rollback(tmp_path, request_id):
    db = tmp_path / "managed-revoke-request.sqlite3"
    _資料庫(db)
    _新增(db)
    with pytest.raises(憑證管理操作錯誤) as error:
        SQLite憑證管理服務(db, 時鐘=lambda: 150.0).撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="owner-1",
            是否管理者=False, 請求識別碼=request_id,
        )
    assert type(error.value) is 憑證管理操作錯誤 and error.value.__context__ is None
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT revoked_at,revision FROM endpoint_credentials").fetchone() == (None, 0)
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (0,)
