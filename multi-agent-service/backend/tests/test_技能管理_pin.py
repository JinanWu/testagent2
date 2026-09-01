"""測試 skill_manage 與 技能使用量 的整合：create 生成 skill_id、delete 的 pin 防線。"""

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能使用量, 技能管理

技能內容 = "---\nname: demo\ndescription: d\n---\n\n# T\n步驟。\n"


@pytest.fixture
def 假環境(tmp_path, monkeypatch):
    """把使用者技能根目錄導向 tmp（技能管理與技能使用量/基本工具都要指到同一處）。"""
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    monkeypatch.setattr(技能管理, "使用者技能根目錄", lambda: 根目錄)
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    return 根目錄


def test_create生成skill_id並初始化記錄(假環境):
    結果 = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})
    assert 結果["success"] is True
    skill_id = 結果["skill_id"]
    assert skill_id
    assert skill_id in 技能使用量.讀取全部技能使用量()  # 記錄以 skill_id 為 key


def test_pin住的技能拒絕刪除且檔案仍在(假環境):
    skill_id = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})["skill_id"]
    技能使用量.設定技能Pin(skill_id, True)
    結果 = 技能管理.管理技能({"action": "delete", "name": "demo"})
    assert 結果["success"] is False
    assert "pin" in 結果["error"]
    assert (假環境 / "demo" / "SKILL.md").exists()


def test_unpin後可刪除並清除使用量記錄(假環境):
    skill_id = 技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})["skill_id"]
    技能使用量.設定技能Pin(skill_id, True)
    技能使用量.設定技能Pin(skill_id, False)
    結果 = 技能管理.管理技能({"action": "delete", "name": "demo"})
    assert 結果["success"] is True
    assert not (假環境 / "demo").exists()
    assert skill_id not in 技能使用量.讀取全部技能使用量()


def test_未pin的技能可正常刪除(假環境):
    技能管理.管理技能({"action": "create", "name": "demo", "content": 技能內容})
    結果 = 技能管理.管理技能({"action": "delete", "name": "demo"})
    assert 結果["success"] is True
    assert not (假環境 / "demo").exists()
