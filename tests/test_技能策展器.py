"""測試技能策展器（Curator）：彙總事件、生命週期轉移、pin 保護、封存。"""

from datetime import datetime, timedelta, timezone

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能使用事件, 技能使用量, 技能策展器


@pytest.fixture
def 假環境(tmp_path, monkeypatch):
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    return 根目錄


def _建立技能(根目錄, name, skill_id):
    目錄 = 根目錄 / name
    目錄.mkdir(parents=True, exist_ok=True)
    (目錄 / "SKILL.md").write_text(
        f"---\nid: {skill_id}\nname: {name}\ndescription: d\n---\n\n# T\n步驟。\n",
        encoding="utf-8",
    )


def test_彙總事件到使用量覆寫use_count與last_used(假環境):
    _建立技能(假環境, "demo", "sid-1")
    技能使用事件.記錄事件("sid-1", "alice", used_at="2026-07-01T00:00:00+00:00")
    技能使用事件.記錄事件("sid-1", "alice", used_at="2026-07-05T00:00:00+00:00")
    更新數 = 技能策展器.彙總事件到使用量()
    assert 更新數 == 1
    記錄 = 技能使用量.取得技能使用量記錄("sid-1")
    assert 記錄["use_count"] == 2
    assert 記錄["last_used_at"] == "2026-07-05T00:00:00+00:00"
    assert 記錄["user_id"] == "alice"


def test_彙總不重建已刪除技能的殭屍記錄(假環境):
    # 事件存在，但技能已不在 user_skill（已刪除）→ 不應建立記錄
    技能使用事件.記錄事件("sid-gone", "alice")
    assert 技能策展器.彙總事件到使用量() == 0
    assert "sid-gone" not in 技能使用量.讀取全部技能使用量()


def test_閒置技能被標記為stale(假環境, monkeypatch):
    monkeypatch.setenv("TESTAGENT2_SKILL_STALE_DAYS", "30")
    monkeypatch.setenv("TESTAGENT2_SKILL_ARCHIVE_DAYS", "60")
    _建立技能(假環境, "demo", "sid-1")
    # 40 天前用過 → 超過 stale 門檻、未達 archive
    技能使用量.設定技能使用量彙總("sid-1", 1, (datetime.now(timezone.utc) - timedelta(days=40)).isoformat())
    統計 = 技能策展器.套用生命週期轉移()
    assert 統計["marked_stale"] == 1
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_閒置


def test_久未使用技能被封存並搬到archive(假環境, monkeypatch):
    monkeypatch.setenv("TESTAGENT2_SKILL_ARCHIVE_DAYS", "60")
    _建立技能(假環境, "demo", "sid-1")
    技能使用量.設定技能使用量彙總("sid-1", 1, (datetime.now(timezone.utc) - timedelta(days=90)).isoformat())
    統計 = 技能策展器.套用生命週期轉移()
    assert 統計["archived"] == 1
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_封存
    assert not (假環境 / "demo").exists()          # 已搬走
    assert (假環境 / ".archive" / "demo" / "SKILL.md").exists()  # 在封存倉庫


def test_pin住的技能永不轉移(假環境, monkeypatch):
    monkeypatch.setenv("TESTAGENT2_SKILL_ARCHIVE_DAYS", "60")
    _建立技能(假環境, "demo", "sid-1")
    技能使用量.設定技能使用量彙總("sid-1", 1, (datetime.now(timezone.utc) - timedelta(days=999)).isoformat())
    技能使用量.設定技能Pin("sid-1", True)
    統計 = 技能策展器.套用生命週期轉移()
    assert 統計["skipped_pinned"] == 1
    assert 統計["archived"] == 0
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_使用中
    assert (假環境 / "demo").exists()  # 沒被搬走


def test_stale技能又被使用會復活(假環境, monkeypatch):
    monkeypatch.setenv("TESTAGENT2_SKILL_STALE_DAYS", "30")
    monkeypatch.setenv("TESTAGENT2_SKILL_ARCHIVE_DAYS", "60")
    _建立技能(假環境, "demo", "sid-1")
    技能使用量.設定技能使用量彙總("sid-1", 1, (datetime.now(timezone.utc) - timedelta(days=40)).isoformat())
    技能使用量.設定技能生命狀態("sid-1", 技能使用量.技能生命狀態_閒置)
    # 最近又用過 → 錨點變新 → 復活
    技能使用量.設定技能使用量彙總("sid-1", 2, datetime.now(timezone.utc).isoformat())
    統計 = 技能策展器.套用生命週期轉移()
    assert 統計["reactivated"] == 1
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_使用中


def test_新建技能不會立刻被封存(假環境):
    # 沒有 last_used_at，但 created_at 是現在 → 錨點是現在 → 不動
    _建立技能(假環境, "demo", "sid-1")
    技能使用量.初始化技能使用量記錄("sid-1", user_id="alice")
    統計 = 技能策展器.套用生命週期轉移()
    assert 統計["archived"] == 0
    assert 統計["marked_stale"] == 0


def test_執行策展整合彙總與轉移(假環境, monkeypatch):
    monkeypatch.setenv("TESTAGENT2_SKILL_ARCHIVE_DAYS", "60")
    _建立技能(假環境, "demo", "sid-1")
    技能使用事件.記錄事件("sid-1", "alice", used_at=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat())
    結果 = 技能策展器.執行策展()
    assert 結果["aggregated"] == 1
    assert 結果["archived"] == 1
