"""發布介面資料庫 manifest discovery 與 fresh 初始化測試。"""

import os
import sqlite3
from pathlib import Path

import pytest

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.發布介面 import 資料庫 as 發布資料庫
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.遷移執行器 import 執行遷移, 遷移執行錯誤


def _q(db: Path, sql: str):
    """執行唯讀查詢並回傳所有資料列。"""
    with sqlite3.connect(db) as 連線:
        return 連線.execute(sql).fetchall()


def _assert_發布遷移完成(db: Path) -> None:
    """確認發布介面ledger與核心資料表已建立。"""
    assert _q(db, "SELECT version,name FROM published_api_schema_migrations ORDER BY version") == [
        (版本, 檔.name) for 版本, 檔 in enumerate(sorted(Path("繁中代理/發布介面/遷移").glob("*.sql")), 1)
    ]
    assert "service_accounts" in {列[0] for 列 in _q(db, "SELECT name FROM sqlite_master WHERE type='table'")}


def _安全關閉(物件) -> None:
    """安全關閉production store或SQLite連線，避免WAL與transaction影響後續快照。"""
    連線 = getattr(物件, "連線", 物件)
    close = getattr(連線, "close", None)
    if close is not None:
        close()


def _quote_ident(名稱: str) -> str:
    """替SQLite identifier加上雙引號，供測試快照安全查詢指定legacy table。"""
    return '"' + 名稱.replace('"', '""') + '"'


def _existing_legacy_tables(db: Path) -> tuple[str, ...]:
    """從遷移前sqlite_master動態列出既有非sqlite_% legacy表，包含FTS virtual與shadow表。"""
    return tuple(
        name
        for (name,) in _q(
            db,
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """,
        )
    )


def _legacy_table_snapshot(db: Path, 表名清單: tuple[str, ...]) -> dict[str, object]:
    """快照指定legacy表的欄位、rows、sqlite_master SQL與schema_version存在和值。"""
    with sqlite3.connect(db) as 連線:
        連線.row_factory = sqlite3.Row
        tables: dict[str, object] = {}
        for 表名 in 表名清單:
            exists = 連線.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (表名,),
            ).fetchone()
            if exists is None:
                tables[表名] = {"present": False, "columns": (), "rows": ()}
                continue
            columns = tuple(tuple(row) for row in 連線.execute(f"PRAGMA table_xinfo({_quote_ident(表名)})"))
            欄位名稱 = [str(row[1]) for row in columns]
            order_by = ", ".join(_quote_ident(欄位) for 欄位 in 欄位名稱) or "rowid"
            rows = tuple(
                tuple(row)
                for row in 連線.execute(f"SELECT * FROM {_quote_ident(表名)} ORDER BY {order_by}")
            )
            tables[表名] = {"present": True, "columns": columns, "rows": rows}
        master = tuple(
            tuple(row)
            for row in 連線.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                  AND (name IN ({}) OR tbl_name IN ({}))
                ORDER BY type, name, tbl_name
                """.format(",".join("?" for _ in 表名清單), ",".join("?" for _ in 表名清單)),
                tuple(表名清單) + tuple(表名清單),
            )
        )
        schema_version = 連線.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        schema_value = None
        if schema_version is not None:
            schema_value = tuple(tuple(row) for row in 連線.execute("SELECT * FROM schema_version ORDER BY version"))
        return {
            "tables": tables,
            "sqlite_master": master,
            "schema_version": {"present": schema_version is not None, "rows": schema_value},
        }


def _建立使用者與登入(db: Path) -> str:
    """透過production使用者庫建立user、settings與auth token row並回傳真實user id。"""
    庫 = 使用者庫(db)
    try:
        使用者 = 庫.建立使用者("alice", password="pw", enabled_tools=["shell"], allowed_workdirs=["*"])
        token = 庫.建立登入Token(str(使用者["id"]), expires_at=0)
        assert 庫.驗證登入Token(token).user_id == str(使用者["id"])
        return str(使用者["id"])
    finally:
        _安全關閉(庫)


def _建立工作階段與訊息(db: Path, user_id: str | None = None) -> str:
    """透過production工作階段庫建立session與message row並回傳真實session id。"""
    庫 = 工作階段庫(db)
    try:
        session_id = 庫.建立或讀取工作階段("legacy-session", user_id=user_id, model="fake")
        庫.寫入訊息清單(session_id, [{"role": "user", "content": "legacy message"}])
        assert 庫.讀取訊息(session_id, user_id=user_id)[0]["content"] == "legacy message"
        return session_id
    finally:
        _安全關閉(庫)


def test_fresh_empty_db_apply_0001_to_0006_and_idempotent(tmp_path):
    """空資料庫應依序套用六版，重跑保持無操作。"""
    db = tmp_path / "fresh.sqlite3"
    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5, 6)
    assert 初始化發布介面資料庫(db) == ()
    _assert_發布遷移完成(db)
    assert _q(db, "PRAGMA foreign_key_check") == []
    assert _q(db, "PRAGMA foreign_key_list(endpoint_redactions)")[0][2:5] == (
        "audit_events", "audit_event_id", "id"
    )


def test_0006無損保留legacy_audit_row並升級完整事件欄位(tmp_path):
    """v6須保留舊row，將legacy target/time映射到resource/occurred欄位。"""
    db = tmp_path / "audit-upgrade.sqlite3"
    前五版 = 載入發布介面遷移()[:5]
    assert 執行遷移(db, 前五版) == (1, 2, 3, 4, 5)
    with sqlite3.connect(db) as 連線:
        連線.execute(
            "INSERT INTO audit_events("
            "id,actor_type,actor_id,action,target_type,target_id,endpoint_id,request_id,metadata_json,created_at"
            ") VALUES('evt_legacy','system',NULL,'legacy.action','endpoint','ep_1',NULL,'req_1','{}',12.5)"
        )
        連線.execute("INSERT INTO service_accounts VALUES('sa_1',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints("
            "id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at"
            ") VALUES('ep_1','owner_1','sa_1','legacy-endpoint','active',NULL,1,1)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES("
            "'ver_1','ep_1',1,'requirement','prompt','[]','[]','{}','runtime','{}','{}','{}',"
            "NULL,'{}',0,'owner_1',1)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver_1' WHERE id='ep_1'")
        連線.execute(
            "INSERT INTO endpoint_invocations("
            "id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,message_id,"
            "status,input_json,created_at) VALUES("
            "'inv_1','ep_1','ver_1',NULL,'req_inv',NULL,NULL,'succeeded','{}',2)"
        )
        連線.execute(
            "INSERT INTO endpoint_redactions("
            "id,invocation_id,target_type,target_row_id,json_path,original_sha256,reason,"
            "actor_type,actor_id,audit_event_id,is_tombstone,redacted_at) VALUES("
            "'red_1','inv_1','metadata','inv_1','$.secret',"
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
            "'legacy cleanup','system',NULL,'evt_legacy',0,3)"
        )

    assert 初始化發布介面資料庫(db) == (6,)
    assert _q(
        db,
        "SELECT event_id,resource_type,resource_id,occurred_at,outcome,invocation_id,created_at "
        "FROM audit_events",
    ) == [("evt_legacy", "endpoint", "ep_1", 12.5, "legacy_unknown", None, 12.5)]
    assert _q(
        db,
        "SELECT id,invocation_id,target_type,target_row_id,json_path,original_sha256,reason,"
        "actor_type,actor_id,audit_event_id,is_tombstone,redacted_at "
        "FROM endpoint_redactions",
    ) == [(
        "red_1", "inv_1", "metadata", "inv_1", "$.secret",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "legacy cleanup", "system", None, "evt_legacy", 0, 3.0,
    )]
    assert _q(db, "PRAGMA foreign_key_check") == []
    assert _q(db, "PRAGMA foreign_key_list(endpoint_redactions)")[0][2:5] == (
        "audit_events", "audit_event_id", "id"
    )
    索引列 = _q(db, "PRAGMA index_list(endpoint_redactions)")
    assert {列[1] for 列 in 索引列 if 列[3] == "c"} == {
        "idx_endpoint_redactions_invocation_time", "idx_endpoint_redactions_audit"
    }
    unique名稱 = next(列[1] for 列 in 索引列 if 列[2] == 1 and 列[3] == "u")
    assert [列[2] for 列 in _q(db, f'PRAGMA index_info("{unique名稱}")')] == [
        "target_type", "target_row_id", "json_path"
    ]
    with sqlite3.connect(db) as 連線:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            連線.execute(
                "INSERT INTO endpoint_redactions("
                "id,invocation_id,target_type,target_row_id,json_path,original_sha256,reason,"
                "actor_type,actor_id,audit_event_id,is_tombstone,redacted_at) "
                "SELECT 'red_duplicate',invocation_id,target_type,target_row_id,json_path,"
                "original_sha256,reason,actor_type,actor_id,audit_event_id,is_tombstone,redacted_at "
                "FROM endpoint_redactions WHERE id='red_1'"
            )
    with sqlite3.connect(db) as 連線:
        with pytest.raises(sqlite3.IntegrityError, match="audit events are append only"):
            連線.execute("UPDATE audit_events SET outcome='success' WHERE event_id='evt_legacy'")
        with pytest.raises(sqlite3.IntegrityError, match="audit events are append only"):
            連線.execute("DELETE FROM audit_events WHERE event_id='evt_legacy'")


def test_users_auth_only_db_發布遷移不改legacy_user_tables(tmp_path):
    """users/auth-only舊DB套用發布遷移後，使用者legacy表與schema_version absence完全不變。"""
    db = tmp_path / "users-auth.sqlite3"
    _建立使用者與登入(db)
    legacy_tables = _existing_legacy_tables(db)
    assert {"users", "user_settings", "auth_sessions"} <= set(legacy_tables)
    before = _legacy_table_snapshot(db, legacy_tables)
    assert before["schema_version"] == {"present": False, "rows": None}

    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5, 6)
    assert 初始化發布介面資料庫(db) == ()

    after = _legacy_table_snapshot(db, legacy_tables)
    assert after == before
    assert after["schema_version"] == {"present": False, "rows": None}
    assert _q(db, "PRAGMA foreign_key_check") == []


def test_sessions_messages_only_db_發布遷移不改legacy_session_tables(tmp_path):
    """sessions/messages-only舊DB套用發布遷移後，schema_version、sessions與messages完整不變。"""
    db = tmp_path / "sessions-messages.sqlite3"
    _建立工作階段與訊息(db)
    legacy_tables = _existing_legacy_tables(db)
    assert {"schema_version", "sessions", "messages", "state_meta", "compression_locks"} <= set(legacy_tables)
    assert any(表名.startswith("messages_fts") for 表名 in legacy_tables)
    before = _legacy_table_snapshot(db, legacy_tables)
    assert before["schema_version"]["present"] is True

    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5, 6)
    assert 初始化發布介面資料庫(db) == ()

    after = _legacy_table_snapshot(db, legacy_tables)
    assert after == before
    assert _q(db, "PRAGMA foreign_key_check") == []


def test_shared_users_and_sessions_db_發布遷移不改任一legacy_table(tmp_path):
    """同一shared DB內user/auth與session/message並存時，發布遷移只新增published schema。"""
    db = tmp_path / "shared.sqlite3"
    user_id = _建立使用者與登入(db)
    _建立工作階段與訊息(db, user_id=user_id)
    legacy_tables = _existing_legacy_tables(db)
    assert {
        "users",
        "user_settings",
        "auth_sessions",
        "schema_version",
        "sessions",
        "messages",
        "state_meta",
        "compression_locks",
    } <= set(legacy_tables)
    assert any(表名.startswith("messages_fts") for 表名 in legacy_tables)
    before = _legacy_table_snapshot(db, legacy_tables)
    assert before["schema_version"]["present"] is True

    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5, 6)
    assert 初始化發布介面資料庫(db) == ()

    after = _legacy_table_snapshot(db, legacy_tables)
    assert after == before
    _assert_發布遷移完成(db)
    assert _q(db, "PRAGMA foreign_key_check") == []


def test_manifest_sorting_contiguous_duplicate_unknown_utf8_symlink_and_nonregular(tmp_path):
    """Manifest只接受連續版本與安全的一般UTF-8 SQL檔。"""
    d = tmp_path / "m"
    d.mkdir()
    (d / "0002_b.sql").write_text("CREATE TABLE b(id INTEGER);", encoding="utf-8")
    (d / "0001_a.sql").write_text("CREATE TABLE a(id INTEGER);", encoding="utf-8")
    assert [項目.版本 for 項目 in 載入發布介面遷移(d)] == [1, 2]
    cases = [
        ("empty", {}),
        ("gap", {"0002_b.sql": "SELECT 1;"}),
        ("unknown", {"01_bad.sql": "SELECT 1;"}),
        ("dup", {"0001_a.sql": "SELECT 1;", "0001_b.sql": "SELECT 1;"}),
    ]
    for dirname, files in cases:
        case = tmp_path / dirname
        case.mkdir()
        for name, text in files.items():
            (case / name).write_text(text, encoding="utf-8")
        with pytest.raises(遷移執行錯誤) as 錯誤:
            載入發布介面遷移(case)
        assert str(錯誤.value) == "發布介面遷移 manifest 不符合契約"
        assert repr(錯誤.value) == "遷移執行錯誤('發布介面遷移 manifest 不符合契約')"
        assert 錯誤.value.__cause__ is None
        assert 錯誤.value.__suppress_context__ is True
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "0001_bad.sql").write_bytes(b"\xff")
    with pytest.raises(遷移執行錯誤):
        載入發布介面遷移(bad)
    if hasattr(os, "symlink"):
        linkroot = tmp_path / "linkroot"
        os.symlink(d, linkroot)
        with pytest.raises(遷移執行錯誤):
            載入發布介面遷移(linkroot)
        linkfile_dir = tmp_path / "linkfile"
        linkfile_dir.mkdir()
        os.symlink(d / "0001_a.sql", linkfile_dir / "0001_link.sql")
        with pytest.raises(遷移執行錯誤):
            載入發布介面遷移(linkfile_dir)
    nonregular = tmp_path / "nonregular"
    nonregular.mkdir()
    (nonregular / "0001_dir.sql").mkdir()
    with pytest.raises(遷移執行錯誤):
        載入發布介面遷移(nonregular)


def test_loader_returns_only_pending_and_does_not_read_applied_sql(tmp_path, monkeypatch):
    """Loader只回傳pending版本，且不再讀取已套用SQL。"""
    db = tmp_path / "done.sqlite3"
    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5, 6)

    def fail_read(*_args, **_kwargs):
        """若測試期間再次讀取SQL便立即失敗。"""
        raise AssertionError("不應讀取已套用 SQL")

    monkeypatch.setattr(發布資料庫, "_讀取一般檔文字", fail_read)
    assert 載入發布介面遷移(資料庫路徑=db) == ()
    assert 初始化發布介面資料庫(db) == ()


def test_loader_missing_db_is_read_only_and_returns_all_pending(tmp_path):
    """不存在的ledger資料庫不可被loader建立，且所有manifest版本仍為pending。"""
    db = tmp_path / "missing.sqlite3"
    assert not db.exists()
    pending = 載入發布介面遷移(資料庫路徑=db)
    assert [項目.版本 for 項目 in pending] == [1, 2, 3, 4, 5, 6]
    assert not db.exists()


def test_manifest_root_fd_pinned_across_enumeration_and_read(tmp_path, monkeypatch):
    """列舉後即使原路徑被替換，loader仍只能讀取已釘住的原始manifest目錄。"""
    root = tmp_path / "manifest"
    moved = tmp_path / "manifest.original"
    root.mkdir()
    (root / "0001_original.sql").write_text("CREATE TABLE original(id INTEGER);", encoding="utf-8")

    def replace_root_after_enumeration(root_fd):
        """在列舉完成後替換原路徑，檢查後續讀取不會回到路徑解析。"""
        names = os.listdir(root_fd)
        root.rename(moved)
        root.mkdir()
        (root / "0001_original.sql").write_text("CREATE TABLE replacement(id INTEGER);", encoding="utf-8")
        return names

    monkeypatch.setattr(發布資料庫, "_列舉目錄名稱", replace_root_after_enumeration)
    pending = 載入發布介面遷移(root)
    assert len(pending) == 1
    assert pending[0].SQL == "CREATE TABLE original(id INTEGER);"


def test_manifest_loader_fails_closed_without_required_fd_capabilities(tmp_path, monkeypatch):
    """平台缺少O_NOFOLLOW/O_DIRECTORY或open(dir_fd)支援時，manifest loader必須固定拒絕。"""
    root = tmp_path / "manifest"
    root.mkdir()
    (root / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    cases = [
        ("O_NOFOLLOW", None, None, None),
        ("O_DIRECTORY", None, None, None),
        (None, set(), None, None),
        (None, None, set(), None),
    ]
    for missing_attr, supports_dir_fd, supports_fd, message in cases:
        if missing_attr is not None and not hasattr(os, missing_attr):
            continue
        with monkeypatch.context() as patch:
            if missing_attr is not None:
                patch.delattr(os, missing_attr)
            if supports_dir_fd is not None:
                patch.setattr(os, "supports_dir_fd", supports_dir_fd)
            if supports_fd is not None:
                patch.setattr(os, "supports_fd", supports_fd)
            with pytest.raises(遷移執行錯誤) as 錯誤:
                載入發布介面遷移(root)
            assert str(錯誤.value) == "發布介面遷移 manifest 不符合契約"
            assert 錯誤.value.__cause__ is message
    assert [項目.版本 for 項目 in 載入發布介面遷移(root)] == [1]
