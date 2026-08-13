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


def _建立基準(資料庫):
    """建立兩組 endpoint／service-account authority 的正式測試資料。

    參數：``資料庫`` 為 pytest 隔離 SQLite 路徑。
    返回值：無；完成 migration 與兩組 immutable version graph。
    """
    初始化發布介面資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
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
    """驗證 fresh、upgrade 與 rerun 得到同一 migration 13 結構。

    參數：``tmp_path`` 提供隔離資料庫路徑。
    返回值：無；所有結構與 ledger assertions 必須通過。
    """
    新資料庫 = tmp_path / "fresh.db"
    assert 初始化發布介面資料庫(新資料庫) == tuple(range(1, 14))
    assert 初始化發布介面資料庫(新資料庫) == ()
    升級資料庫 = tmp_path / "upgrade.db"
    assert 執行遷移(升級資料庫, 載入發布介面遷移()[:12]) == tuple(range(1, 13))
    assert 初始化發布介面資料庫(升級資料庫) == (13,)
    with sqlite3.connect(升級資料庫) as 連線:
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []
        欄位 = [列[1] for 列 in 連線.execute("PRAGMA table_info(published_session_turn_pairs)")]
        assert 欄位 == ["endpoint_id", "service_account_id", "session_id", "sequence_number", "endpoint_version_id", "user_message_json", "assistant_message_json", "pair_size_bytes", "token_count", "created_at"]


def test_scope不修改既有端點索引且錯配寫入fail_closed(tmp_path):
    """驗證新 scope 不侵入舊 endpoint index 且錯配寫入 fail closed。

    參數：``tmp_path`` 提供隔離資料庫。
    返回值：無；schema inventory 與 trigger assertions 必須通過。
    """
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
    """驗證法定 endpoint retention 可 cascade 清除 session history。

    參數：``tmp_path`` 提供隔離資料庫。
    返回值：無；刪除 graph 後 history row 必須為零。
    """
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
    """驗證 composite scope、stale CAS、restart 與跨 scope 隔離。

    參數：``tmp_path`` 提供 durable restart 資料庫。
    返回值：無；有序讀回與隔離 assertions 必須通過。
    """
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
    """驗證 turns／bytes／tokens bounds 與 corrupt row fail closed。

    參數：``tmp_path`` 提供隔離資料庫。
    返回值：無；bounded read 與損毀拒絕 assertions 必須通過。
    """
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


@pytest.mark.parametrize("限制欄位", [
    "pair_size_bytes",
    "token_count",
])
def test_aggregate_bytes_tokens超限只保留最新完整pairs(tmp_path, 限制欄位):
    """驗證 aggregate bytes/token cap 不截斷 pair，只捨棄更舊完整 pair。

    參數：隔離 DB 與要驗證的 aggregate ledger 欄位。
    返回值：無；readback 只含最新一組且 aggregate 不超界。
    """
    db = tmp_path / f"aggregate-{限制欄位}.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    大內容 = "甲" * 44_000 if 限制欄位 == "pair_size_bytes" else "a"
    單筆TOKEN = 最大歷史TOKEN數 // 2 + 1 if 限制欄位 == "token_count" else 1
    for sequence in (1, 2):
        repo.附加成功對話組(
            "ep1", "sa1", "bounded", "v1", {"role": "user", "content": str(sequence)},
            {"role": "assistant", "content": 大內容}, 單筆TOKEN, expected_sequence=sequence,
        )
    結果 = repo.讀取成功歷史("ep1", "sa1", "bounded")
    assert [組.sequence_number for 組 in 結果] == [2]
    上限 = 最大歷史位元組 if 限制欄位 == "pair_size_bytes" else 最大歷史TOKEN數
    assert sum(getattr(組, 限制欄位) for 組 in 結果) <= 上限
    with sqlite3.connect(db) as 連線:
        全部值 = [列[0] for 列 in 連線.execute(
            f"SELECT {限制欄位} FROM published_session_turn_pairs ORDER BY sequence_number"
        )]
    assert len(全部值) == 2 and sum(全部值) > 上限


def test_sequence_gap_history_fail_closed(tmp_path):
    """驗證最新 bounded window 內 sequence gap 一律視為 corrupt history。

    參數：``tmp_path`` 提供隔離 SQLite authority。
    返回值：無；讀取必須固定拋 ``Published工作階段錯誤``。
    """
    db = tmp_path / "gap.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    for sequence in (1, 2, 3):
        repo.附加成功對話組(
            "ep1", "sa1", "gap", "v1", {"role": "user"}, {"role": "assistant"},
            1, expected_sequence=sequence,
        )
    with sqlite3.connect(db) as 連線:
        連線.execute("DELETE FROM published_session_turn_pairs WHERE sequence_number=2")
    with pytest.raises(Published工作階段錯誤):
        repo.讀取成功歷史("ep1", "sa1", "gap")


def test_較舊區段sequence_gap即使不在最新bounded_window仍fail_closed(tmp_path):
    """驗證 turns cap 之外的舊 sequence gap 仍由全 scope 摘要偵測。

    參數：``tmp_path`` 提供超過 32 turns 的隔離 SQLite history。
    返回值：無；刪除舊序號 2 後讀取必須固定拒絕。
    """
    db = tmp_path / "old-gap.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    for sequence in range(1, 35):
        repo.附加成功對話組(
            "ep1", "sa1", "old-gap", "v1", {"role": "user"}, {"role": "assistant"},
            1, expected_sequence=sequence,
        )
    with sqlite3.connect(db) as 連線:
        連線.execute("DELETE FROM published_session_turn_pairs WHERE sequence_number=2")
    with pytest.raises(Published工作階段錯誤):
        repo.讀取成功歷史("ep1", "sa1", "old-gap")


@pytest.mark.parametrize("欄位,損毀訊息", [
    ("user_message_json", '{"content":"x","role":"system"}'),
    ("assistant_message_json", '{"content":"x","role":"system"}'),
    ("user_message_json", '{"content":"x","metadata":{},"role":"user"}'),
])
def test_corrupt_history_role或額外欄位不得注入system_prompt(tmp_path, 欄位, 損毀訊息):
    """驗證 durable message role/shape 損毀在 runtime 前 fail closed。

    參數：隔離 DB、要竄改的 JSON 欄位與 canonical corrupt message。
    返回值：無；repository read 必須固定拒絕，不回傳可注入訊息。
    """
    db = tmp_path / f"role-{欄位}.db"
    _建立基準(db)
    repo = SQLitePublished工作階段儲存庫(db)
    repo.附加成功對話組(
        "ep1", "sa1", "role", "v1", {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"}, 1, expected_sequence=1,
    )
    with sqlite3.connect(db) as 連線:
        連線.execute("DROP TRIGGER published_session_turn_pairs_no_update")
        另一欄 = "assistant_message_json" if 欄位 == "user_message_json" else "user_message_json"
        原值 = 連線.execute(f"SELECT {另一欄} FROM published_session_turn_pairs").fetchone()[0]
        大小 = len(損毀訊息.encode("utf-8")) + len(原值.encode("utf-8"))
        連線.execute(
            f"UPDATE published_session_turn_pairs SET {欄位}=?,pair_size_bytes=?",
            (損毀訊息, 大小),
        )
    with pytest.raises(Published工作階段錯誤):
        repo.讀取成功歷史("ep1", "sa1", "role")
