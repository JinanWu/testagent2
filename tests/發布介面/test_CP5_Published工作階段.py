"""A09-02 Published 工作階段 durable repository 驗收。"""

import sqlite3

import pytest

from 繁中代理.發布介面.呼叫.Published工作階段 import (
    Published工作階段錯誤,
    SQLitePublished工作階段儲存庫,
    最大成功對話組數,
    最大歷史TOKEN數,
    最大歷史位元組,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.遷移執行器 import 執行遷移


def _建立基準(db):
    初始化發布介面資料庫(db)
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.executemany("INSERT INTO service_accounts(id,created_at) VALUES(?,1)", [("sa1",), ("sa2",)])
        連線.executemany(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES(?,?,?,?, 'active',1,1)",
            [("ep1", "owner1", "sa1", "one"), ("ep2", "owner2", "sa2", "two")],
        )
        for version, endpoint, owner in (("v1", "ep1", "owner1"), ("v2", "ep2", "owner2")):
            連線.execute(
                "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json,schema_changed,created_by_user_id,created_at) VALUES(?,?,1,'r','p','[]','[]','{}','rev','{}','{}','{}','{}',0,?,1)",
                (version, endpoint, owner),
            )


def test_fresh_upgrade重跑與結構契約(tmp_path):
    fresh = tmp_path / "fresh.db"
    assert 初始化發布介面資料庫(fresh) == tuple(range(1, 14))
    assert 初始化發布介面資料庫(fresh) == ()
    upgrade = tmp_path / "upgrade.db"
    assert 執行遷移(upgrade, 載入發布介面遷移()[:12]) == tuple(range(1, 13))
    assert 初始化發布介面資料庫(upgrade) == (13,)
    with sqlite3.connect(upgrade) as 連線:
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []
        欄位 = [列[1] for 列 in 連線.execute("PRAGMA table_info(published_session_turn_pairs)")]
        assert 欄位 == ["endpoint_id", "service_account_id", "session_id", "sequence_number", "endpoint_version_id", "user_message_json", "assistant_message_json", "pair_size_bytes", "token_count", "created_at"]


def test_scope不修改既有端點索引且錯配寫入fail_closed(tmp_path):
    db = tmp_path / "scope.db"
    _建立基準(db)
    with sqlite3.connect(db) as 連線:
        assert all("service_account" not in 列[1] for 列 in 連線.execute(
            "PRAGMA index_list(published_endpoints)"
        ))
        with pytest.raises(sqlite3.IntegrityError, match="scope mismatch"):
            連線.execute(
                "INSERT INTO published_session_turn_pairs VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("ep1", "sa2", "same", 1, "v1", '{"role":"user"}',
                 '{"role":"assistant"}', 40, 1, 1),
            )


def test_endpoint法定清除可連同session_history_cascade(tmp_path):
    db = tmp_path / "retention.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    repo.附加成功對話組(
        "ep1", "sa1", "case", "v1", {"role": "user"}, {"role": "assistant"},
        1, expected_sequence=1,
    )
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("DROP TRIGGER published_endpoint_versions_no_delete")
        連線.execute("DELETE FROM published_endpoint_versions WHERE endpoint_id='ep1'")
        連線.execute("DELETE FROM published_endpoints WHERE id='ep1'")
        assert 連線.execute(
            "SELECT COUNT(*) FROM published_session_turn_pairs WHERE endpoint_id='ep1'"
        ).fetchone() == (0,)


def test_composite_scope_CAS_restart與跨scope隔離(tmp_path):
    db = tmp_path / "history.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db, 時鐘=lambda: 5)
    assert repo.讀取成功歷史("ep1", "sa1", "same") == ()
    assert repo.附加成功對話組("ep1", "sa1", "same", "v1", {"role": "user", "content": "一"}, {"role": "assistant", "content": "答"}, 4, expected_sequence=1) == 1
    assert repo.附加成功對話組("ep1", "sa1", "same", "v1", {"role": "user", "content": "二"}, {"role": "assistant", "content": "答二"}, 5, expected_sequence=2) == 2
    with pytest.raises(Published工作階段錯誤):
        repo.附加成功對話組("ep1", "sa1", "same", "v1", {"role": "user"}, {"role": "assistant"}, 1, expected_sequence=2)
    assert [組.sequence_number for 組 in SQLitePublished工作階段儲存庫(db).讀取成功歷史("ep1", "sa1", "same")] == [1, 2]
    assert repo.讀取成功歷史("ep2", "sa2", "same") == ()
    assert repo.讀取成功歷史("ep1", "sa2", "same") == ()


def test_bounds與損毀fail_closed(tmp_path):
    assert (最大成功對話組數, 最大歷史位元組, 最大歷史TOKEN數) == (32, 262144, 32768)
    db = tmp_path / "bounds.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    for sequence in range(1, 35):
        repo.附加成功對話組("ep1", "sa1", "s", "v1", {"role": "user", "content": str(sequence)}, {"role": "assistant", "content": "a"}, 1, expected_sequence=sequence)
    assert len(repo.讀取成功歷史("ep1", "sa1", "s")) == 32
    with sqlite3.connect(db) as 連線:
        連線.execute("DROP TRIGGER published_session_turn_pairs_no_update")
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE published_session_turn_pairs SET pair_size_bytes=1 WHERE sequence_number=34")
    with pytest.raises(Published工作階段錯誤):
        repo.讀取成功歷史("ep1", "sa1", "s")
