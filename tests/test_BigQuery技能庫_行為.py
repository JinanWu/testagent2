"""測試 BigQuery 技能模式下的列出隔離、錯誤轉換與 pin 租戶隔離。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from 繁中代理 import 基本工具
from 繁中代理.工具集 import 技能管理


@pytest.fixture
def 假使用者技能根目錄(tmp_path, monkeypatch):
    """建立本機 user_skill 殘留目錄。"""
    根目錄 = tmp_path / "assets" / "user_skill"
    根目錄.mkdir(parents=True)
    (根目錄 / "local-only").mkdir(parents=True)
    (根目錄 / "local-only" / "SKILL.md").write_text(
        "---\nid: sid-local\nname: local-only\ndescription: d\n---\n\n# T\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(基本工具, "使用者技能根目錄", lambda: 根目錄)
    monkeypatch.setattr(基本工具, "內建技能根目錄", lambda: tmp_path / "assets" / "hermes_skills")
    return 根目錄


def test_列出技能_bigquery模式略過本機user_skill(假使用者技能根目錄, monkeypatch):
    """BigQuery 模式不應把本機 user_skill 殘留與 BQ 列重複列出。"""
    假庫 = MagicMock()
    假庫.列出技能身分.return_value = [
        {"skill_id": "sid-bq", "name": "local-only", "category": None},
    ]
    monkeypatch.setattr(基本工具, "_取得雲端技能庫", lambda: 假庫)

    參數 = {"_skill_roots": None, "_current_user_id": "alice"}
    結果 = 基本工具.列出技能(參數)
    名稱清單 = [項目["name"] for 項目 in 結果["skills"]]
    assert 名稱清單.count("local-only") == 1
    assert any(項目["path"] is None for 項目 in 結果["skills"] if 項目["name"] == "local-only")


def test_列出使用者技能身分_限定使用者時傳遞過濾(假使用者技能根目錄, monkeypatch):
    """列出使用者技能身分應把 user_id / 限定使用者 傳給 BigQuery 層。"""
    假庫 = MagicMock()
    假庫.列出技能身分.return_value = [{"skill_id": "sid-a", "name": "a"}]
    monkeypatch.setattr(基本工具, "_取得雲端技能庫", lambda: 假庫)

    清單 = 基本工具.列出使用者技能身分(user_id="alice", 限定使用者=True)
    assert len(清單) == 1
    假庫.列出技能身分.assert_called_once_with(user_id="alice", 限定使用者=True)


def test_建立技能_bigquery失敗回傳結構化錯誤(monkeypatch):
    """BigQuery 建立失敗應回 success:false，而非讓例外穿透。"""
    假庫 = MagicMock()
    假庫.讀取技能內容.return_value = None

    def 建立失敗(*_args, **_kwargs):
        raise RuntimeError("bq insert failed")

    假庫.建立技能.side_effect = 建立失敗
    monkeypatch.setattr(技能管理, "_取得雲端技能庫", lambda: 假庫)

    內容 = "---\nname: demo\ndescription: d\n---\n\n# T\n步驟。\n"
    結果 = 技能管理.管理技能({"action": "create", "name": "demo", "content": 內容})
    assert 結果["success"] is False
    assert "建立技能失敗" in 結果["error"]
    assert "bq insert failed" in 結果["error"]
