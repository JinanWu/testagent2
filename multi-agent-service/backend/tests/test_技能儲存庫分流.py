"""技能儲存庫應隨 STORAGE_BACKEND 選擇正確 adapter。"""

import sys
from types import ModuleType, SimpleNamespace

from 繁中代理 import BigQuery技能庫 as 技能庫模組
from 繁中代理 import 環境設定


def test_postgres技能儲存庫使用PostgreSQL_adapter(monkeypatch):
    設定 = SimpleNamespace(後端="postgres")
    假模組 = ModuleType("繁中代理.PostgreSQL技能庫")

    class 假PostgreSQL技能庫:
        def __init__(self, 凍結設定):
            self.凍結設定 = 凍結設定

    假模組.PostgreSQL技能庫 = 假PostgreSQL技能庫
    monkeypatch.setattr(環境設定, "讀取交易儲存設定", lambda: 設定)
    monkeypatch.setitem(sys.modules, "繁中代理.PostgreSQL技能庫", 假模組)

    結果 = 技能庫模組.取得啟用中的技能庫()

    assert isinstance(結果, 假PostgreSQL技能庫)
    assert 結果.凍結設定 is 設定
