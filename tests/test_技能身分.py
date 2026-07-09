"""測試技能身分（skill_id）：create 注入、edit 保留、skill_view 以 skill_id 記事件。"""

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能使用事件, 技能管理

技能內容 = "---\nname: demo\ndescription: d\n---\n\n# T\n步驟。\n"


@pytest.fixture
def 假環境(tmp_path, monkeypatch):
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    monkeypatch.setattr(技能管理, "使用者技能根目錄", lambda: 根目錄)
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    return 根目錄


def test_create把skill_id注入frontmatter(假環境):
    skill_id = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})["skill_id"]
    skill_md = 假環境 / "demo" / "SKILL.md"
    assert 基本工具.讀取技能skill_id(skill_md) == skill_id
    assert f"id: {skill_id}" in skill_md.read_text(encoding="utf-8")


def test_edit全量改寫仍保留原skill_id(假環境):
    skill_id = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})["skill_id"]
    新內容 = "---\nname: demo\ndescription: 改過了\n---\n\n# 新\n新步驟。\n"  # 沒有 id
    結果 = 技能管理.管理技能({"action": "edit", "name": "demo", "content": 新內容})
    assert 結果["success"] is True
    # 改寫後身分不變、歷史不斷
    assert 基本工具.讀取技能skill_id(假環境 / "demo" / "SKILL.md") == skill_id


def test_skill_view成功以skill_id記一筆事件(假環境):
    skill_id = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})["skill_id"]
    參數 = {"name": "demo", "_current_user_id": "alice", "_skill_roots": [str(假環境)]}
    基本工具.讀取技能(參數)
    事件 = 技能使用事件.讀取所有事件()
    assert len(事件) == 1
    assert 事件[0]["skill_id"] == skill_id  # 記 skill_id，不是 name
    assert 事件[0]["user_id"] == "alice"


def test_skill_view找不到技能不記事件(假環境):
    技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})
    參數 = {"name": "不存在", "_current_user_id": "alice", "_skill_roots": [str(假環境)]}
    with pytest.raises(FileNotFoundError):
        基本工具.讀取技能(參數)
    assert 技能使用事件.讀取所有事件() == []


def test_create自動用參數name補進frontmatter(假環境):
    """重現實際失敗:模型寫 title、漏了 name → create 仍應成功並補上 name: <參數>。"""
    內容 = "---\ntitle: Google Maps Reviews\ndescription: d\ncategory: general\n---\n\n# T\n步驟。\n"
    結果 = 技能管理.管理技能({"action": "create", "name": "google-maps-reviews", "content": 內容})
    assert 結果["success"] is True
    文字 = (假環境 / "google-maps-reviews" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: google-maps-reviews" in 文字  # 用參數補上了
    assert 基本工具.讀取技能skill_id(假環境 / "google-maps-reviews" / "SKILL.md")  # id 也在


def test_frontmatter缺name且無法解析仍擋下(假環境):
    """content 完全沒有 frontmatter(沒 --- )→ 補不進去 → 仍應被驗證擋下。"""
    結果 = 技能管理.管理技能({"action": "create", "name": "bad", "content": "沒有 frontmatter 的純文字"})
    assert 結果["success"] is False
    assert "frontmatter" in 結果["error"] or "---" in 結果["error"]


def test_stepC擋掉把skill寫成code(假環境):
    """把 skill 寫成工具規格(parameters/implementation/函式)應被擋下,且訊息可行動。"""
    壞內容 = "---\nname: x\ndescription: d\nparameters:\n  - name: q\n---\n\nimplementation: |\n  def f(): pass\n"
    結果 = 技能管理.管理技能({"action": "create", "name": "x", "content": 壞內容})
    assert 結果["success"] is False
    assert "code" in 結果["error"] or "函式" in 結果["error"]


def test_stepC不誤擋正常步驟型skill(假環境):
    好內容 = "---\nname: y\ndescription: d\n---\n\n# 標題\n## 步驟\n1. 用 web_search 搜尋\n2. 回報結果\n"
    結果 = 技能管理.管理技能({"action": "create", "name": "y", "content": 好內容})
    assert 結果["success"] is True


def test_create擋下與內建技能同名(假環境, tmp_path, monkeypatch):
    """與內建技能同名時應拒絕建立，避免 skills_list / skill_view 命名衝突。"""
    內建根目錄 = tmp_path / "assets" / "hermes_skills"
    內建技能目錄 = 內建根目錄 / "bundled-demo"
    內建技能目錄.mkdir(parents=True)
    (內建技能目錄 / "SKILL.md").write_text(
        "---\nname: bundled-demo\ndescription: d\n---\n\n# T\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(技能管理, "內建技能根目錄", lambda: 內建根目錄)

    結果 = 技能管理.管理技能({"action": "create", "name": "bundled-demo", "content": 技能內容})

    assert 結果["success"] is False
    assert "內建技能" in 結果["error"]
    assert not (假環境 / "bundled-demo").exists()
