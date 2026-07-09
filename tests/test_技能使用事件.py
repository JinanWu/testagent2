"""測試技能使用事件（append-only skill_usage_events.jsonl）。"""

import json

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能使用事件


@pytest.fixture
def 假技能環境(tmp_path, monkeypatch):
    """把使用者技能根目錄導向 tmp，讓事件檔落在 tmp/assets 下。"""
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    return 根目錄


def test_事件檔與user_skill同層且為jsonl(假技能環境):
    路徑 = 技能使用事件.取得技能使用事件檔路徑()
    assert 路徑.name == "skill_usage_events.jsonl"
    assert 路徑.parent == 假技能環境.parent  # assets/


def test_記錄事件帶user_id並可讀回(假技能環境):
    筆數 = 技能使用事件.記錄技能使用事件("demo", "alice")
    assert 筆數 == 1
    事件清單 = 技能使用事件.讀取全部技能使用事件()
    assert len(事件清單) == 1
    assert 事件清單[0]["skill_id"] == "demo"
    assert 事件清單[0]["user_id"] == "alice"
    assert 事件清單[0]["used_at"]


def test_append_only不覆蓋既有(假技能環境):
    技能使用事件.記錄技能使用事件("demo", "alice")
    技能使用事件.記錄技能使用事件("demo", "alice")
    技能使用事件.記錄多筆技能使用事件(["a", "b"], "bob")
    assert len(技能使用事件.讀取全部技能使用事件()) == 4


def test_記錄多筆事件去重由呼叫端負責(假技能環境):
    # 模組不主動去重；呼叫端傳幾個就寫幾筆
    寫入 = 技能使用事件.記錄多筆技能使用事件(["x", "y", "z"], "alice")
    assert 寫入 == 3


def test_空清單或空skill_id不寫入(假技能環境):
    assert 技能使用事件.記錄多筆技能使用事件([], "alice") == 0
    assert 技能使用事件.記錄技能使用事件("", "alice") == 0
    assert 技能使用事件.讀取全部技能使用事件() == []


def test_彙總計數並取最新使用時間(假技能環境):
    技能使用事件.記錄技能使用事件("demo", "alice", used_at="2026-07-01T00:00:00+00:00")
    技能使用事件.記錄技能使用事件("demo", "alice", used_at="2026-07-05T00:00:00+00:00")
    技能使用事件.記錄技能使用事件("demo", "alice", used_at="2026-07-03T00:00:00+00:00")
    彙總 = {(列["user_id"], 列["skill_id"]): 列 for 列 in 技能使用事件.彙總技能使用事件()}
    列 = 彙總[("alice", "demo")]
    assert 列["use_count"] == 3
    assert 列["last_used_at"] == "2026-07-05T00:00:00+00:00"


def test_彙總依user_id與skill_id分列(假技能環境):
    # 同名技能在不同 user 下要分屬不同列
    技能使用事件.記錄技能使用事件("shared-name", "alice")
    技能使用事件.記錄技能使用事件("shared-name", "alice")
    技能使用事件.記錄技能使用事件("shared-name", "bob")
    彙總 = {(列["user_id"], 列["skill_id"]): 列 for 列 in 技能使用事件.彙總技能使用事件()}
    assert 彙總[("alice", "shared-name")]["use_count"] == 2
    assert 彙總[("bob", "shared-name")]["use_count"] == 1


def test_毀損行被略過(假技能環境):
    技能使用事件.記錄技能使用事件("demo", "alice")
    with open(技能使用事件.取得技能使用事件檔路徑(), "a", encoding="utf-8") as f:
        f.write("{ 這行壞掉\n")
    技能使用事件.記錄技能使用事件("demo2", "alice")
    事件清單 = 技能使用事件.讀取全部技能使用事件()
    assert {e["skill_id"] for e in 事件清單} == {"demo", "demo2"}


def test_持久化為jsonl每行一筆(假技能環境):
    技能使用事件.記錄多筆技能使用事件(["a", "b"], "alice")
    行清單 = 技能使用事件.取得技能使用事件檔路徑().read_text(encoding="utf-8").splitlines()
    assert len(行清單) == 2
    assert json.loads(行清單[0])["skill_id"] == "a"


def test_寫入失敗回傳0且不留下部分事件(假技能環境, monkeypatch):
    路徑 = 技能使用事件.取得技能使用事件檔路徑()
    原始_open = open

    class 寫入失敗包裝:
        def __init__(self, 檔案):
            self._檔案 = 檔案

        def write(self, _):
            raise OSError("disk full")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._檔案.close()
            return False

    def 攔截_open(檔案路徑, mode="r", **kwargs):
        if mode == "a" and 檔案路徑 == 路徑:
            return 寫入失敗包裝(原始_open(檔案路徑, mode, **kwargs))
        return 原始_open(檔案路徑, mode, **kwargs)

    monkeypatch.setattr("builtins.open", 攔截_open)
    assert 技能使用事件.記錄多筆技能使用事件(["a", "b", "c"], "alice") == 0
    assert 技能使用事件.讀取全部技能使用事件() == []
