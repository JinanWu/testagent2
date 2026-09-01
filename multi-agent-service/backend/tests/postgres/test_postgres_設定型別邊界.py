"""Block 2R 零 I/O 設定型別與 runtime annotations 邊界測試。"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


def test_純設定模組只匯入標準函式庫值與解析工具():
    模組路徑 = Path(__file__).parents[2] / "繁中代理" / "交易儲存設定.py"
    語法樹 = ast.parse(模組路徑.read_text(encoding="utf-8"))
    匯入根 = set()
    for 節點 in ast.walk(語法樹):
        if isinstance(節點, ast.Import):
            匯入根.update(別名.name.split(".", 1)[0] for 別名 in 節點.names)
        elif isinstance(節點, ast.ImportFrom) and 節點.module:
            匯入根.add(節點.module.split(".", 1)[0])
    assert 匯入根 == {"__future__", "dataclasses", "re", "urllib"}


def test_冷process四個公開輸入annotation可解析且環境仍未載入_Path_resolve為零():
    程式 = r'''
import pathlib
import sys
from typing import get_type_hints

原resolve = pathlib.Path.resolve
呼叫 = []
def 記錄resolve(self, *args, **kwargs):
    呼叫.append(str(self))
    return 原resolve(self, *args, **kwargs)
pathlib.Path.resolve = 記錄resolve

assert "繁中代理.環境設定" not in sys.modules
from 繁中代理.交易儲存設定 import 支援的交易儲存後端, 交易儲存設定
import 繁中代理.PostgreSQL連線 as 連線
assert "繁中代理.環境設定" not in sys.modules
assert 呼叫 == [], 呼叫
assert 連線.交易儲存設定 is 交易儲存設定

for 名稱 in ("建立連線池", "啟動共用連線池", "取得共用連線池", "交易連線"):
    函式 = getattr(連線, 名稱)
    hints = get_type_hints(函式)
    assert hints["凍結設定"] is 交易儲存設定, (名稱, hints)
    assert "繁中代理.環境設定" not in sys.modules
    assert 呼叫 == [], (名稱, 呼叫)

import 繁中代理.環境設定 as 環境設定
assert 環境設定.交易儲存設定 is 交易儲存設定
assert 環境設定.支援的交易儲存後端 is 支援的交易儲存後端
'''
    結果 = subprocess.run(
        [sys.executable, "-c", 程式],
        cwd=os.getcwd(),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert 結果.returncode == 0, 結果.stderr
