"""測試技能使用量 sidecar（skill_usage.json），以 skill_id 為 key。"""

import json

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能使用量


@pytest.fixture
def 假技能環境(tmp_path, monkeypatch):
    """把使用者技能根目錄導向 tmp，讓 skill_usage.json 落在 tmp/assets 下。"""
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    return 根目錄


def _建立技能檔(根目錄, name, skill_id, category=None):
    目錄 = (根目錄 / category / name) if category else (根目錄 / name)
    目錄.mkdir(parents=True, exist_ok=True)
    (目錄 / "SKILL.md").write_text(
        f"---\nid: {skill_id}\nname: {name}\ndescription: d\n---\n\n# T\n步驟。\n",
        encoding="utf-8",
    )
    return 目錄


def test_使用量檔放在assets根目錄與user_skill同層(假技能環境):
    路徑 = 技能使用量.取得技能使用量檔路徑()
    assert 路徑.name == "skill_usage.json"
    assert 路徑.parent == 假技能環境.parent  # assets/


def test_初始化記錄建立預設值並帶user_id(假技能環境):
    技能使用量.初始化技能使用量記錄("sid-1", user_id="alice")
    記錄 = 技能使用量.取得技能使用量記錄("sid-1")
    assert 記錄["use_count"] == 0
    assert 記錄["last_used_at"] is None
    assert 記錄["state"] == 技能使用量.技能生命狀態_使用中
    assert 記錄["pinned"] is False
    assert 記錄["user_id"] == "alice"
    assert 記錄["created_at"]


def test_記錄使用累加並更新最後使用時間(假技能環境):
    技能使用量.初始化技能使用量記錄("sid-1")
    技能使用量.記錄技能使用("sid-1")
    技能使用量.記錄技能使用("sid-1", 次數=2)
    記錄 = 技能使用量.取得技能使用量記錄("sid-1")
    assert 記錄["use_count"] == 3
    assert 記錄["last_used_at"] is not None


def test_pin設定與查詢(假技能環境):
    assert 技能使用量.檢查技能是否Pin("sid-1") is False
    技能使用量.設定技能Pin("sid-1", True)
    assert 技能使用量.檢查技能是否Pin("sid-1") is True
    技能使用量.設定技能Pin("sid-1", False)
    assert 技能使用量.檢查技能是否Pin("sid-1") is False


def test_設定狀態只接受有效值(假技能環境):
    技能使用量.設定技能生命狀態("sid-1", 技能使用量.技能生命狀態_封存)
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_封存
    技能使用量.設定技能生命狀態("sid-1", "不存在的狀態")
    assert 技能使用量.取得技能使用量記錄("sid-1")["state"] == 技能使用量.技能生命狀態_封存  # 未被改動


def test_遺忘移除記錄(假技能環境):
    技能使用量.記錄技能使用("sid-1")
    assert "sid-1" in 技能使用量.讀取全部技能使用量()
    技能使用量.移除技能使用量記錄("sid-1")
    assert "sid-1" not in 技能使用量.讀取全部技能使用量()


def test_使用量報告以skill_id為key並帶name(假技能環境):
    _建立技能檔(假技能環境, "used-skill", "sid-used")
    _建立技能檔(假技能環境, "never-used", "sid-never", category="infra")
    技能使用量.初始化技能使用量記錄("sid-used", user_id="alice")
    技能使用量.設定技能Pin("sid-used", True)
    報告 = {列["skill_id"]: 列 for 列 in 技能使用量.產生技能使用量報告()}
    assert 報告["sid-used"]["name"] == "used-skill"
    assert 報告["sid-used"]["pinned"] is True
    assert 報告["sid-used"]["user_id"] == "alice"
    # 沒有使用記錄的技能仍出現在報告，並帶預設值
    assert 報告["sid-never"]["name"] == "never-used"
    assert 報告["sid-never"]["use_count"] == 0
    assert 報告["sid-never"]["last_used_at"] is None


def test_補齊缺少的技能使用量記錄會寫回sidecar(假技能環境):
    _建立技能檔(假技能環境, "new-skill", "sid-new")
    assert "sid-new" not in 技能使用量.讀取全部技能使用量()
    assert 技能使用量.補齊缺少的技能使用量記錄() == 1
    記錄 = 技能使用量.讀取全部技能使用量()["sid-new"]
    assert 記錄["created_at"]
    assert 記錄["state"] == 技能使用量.技能生命狀態_使用中


def test_使用量報告略過無skill_id的技能(假技能環境):
    # 沒有 frontmatter id 的技能（異常/未經 skill_manage 建立）不入報告
    目錄 = 假技能環境 / "no-id"
    目錄.mkdir()
    (目錄 / "SKILL.md").write_text("---\nname: no-id\ndescription: d\n---\n\n# T\n步驟。\n", encoding="utf-8")
    assert all(列["name"] != "no-id" for 列 in 技能使用量.產生技能使用量報告())


def test_持久化為合法json(假技能環境):
    技能使用量.記錄技能使用("sid-1")
    路徑 = 技能使用量.取得技能使用量檔路徑()
    資料 = json.loads(路徑.read_text(encoding="utf-8"))
    assert 資料["sid-1"]["use_count"] == 1


def test_毀損檔案回傳空dict(假技能環境):
    技能使用量.取得技能使用量檔路徑().write_text("{ 壞掉的 json", encoding="utf-8")
    assert 技能使用量.讀取全部技能使用量() == {}


def test_根型別非dict回傳空dict(假技能環境):
    技能使用量.取得技能使用量檔路徑().write_text("[]", encoding="utf-8")
    assert 技能使用量.讀取全部技能使用量() == {}


def test_毀損檔案時變更不覆寫整檔(假技能環境):
    路徑 = 技能使用量.取得技能使用量檔路徑()
    原始 = "{ 壞掉的 json"
    路徑.write_text(原始, encoding="utf-8")
    技能使用量.記錄技能使用("sid-1")
    assert 路徑.read_text(encoding="utf-8") == 原始
    assert 技能使用量.讀取全部技能使用量() == {}


def test_根型別非dict時變更不覆寫整檔(假技能環境):
    路徑 = 技能使用量.取得技能使用量檔路徑()
    原始 = "[]"
    路徑.write_text(原始, encoding="utf-8")
    技能使用量.設定技能Pin("sid-1", True)
    assert 路徑.read_text(encoding="utf-8") == 原始
