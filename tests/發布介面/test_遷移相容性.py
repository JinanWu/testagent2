"""發布介面資料庫 manifest discovery 與 fresh 初始化測試。"""

import os
import sqlite3
from pathlib import Path

import pytest

from 繁中代理.發布介面 import 資料庫 as 發布資料庫
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.遷移執行器 import 遷移執行錯誤


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


def test_fresh_empty_db_apply_0001_to_0005_and_idempotent(tmp_path):
    """空資料庫應依序套用五版，重跑保持無操作。"""
    db = tmp_path / "fresh.sqlite3"
    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5)
    assert 初始化發布介面資料庫(db) == ()
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
    assert 初始化發布介面資料庫(db) == (1, 2, 3, 4, 5)

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
    assert [項目.版本 for 項目 in pending] == [1, 2, 3, 4, 5]
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
