"""檢查專案自有 Python 物件是否具備文檔字串。

功能：
    掃描 `繁中代理` 套件內的 module、class、function，確認都有 docstring。
    同時檢查常見專案自有函數名稱是否不是純英文。外部固定入口如
    `__init__` 與 CLI argparse 欄位不列入語意命名檢查。

參數：
    無；從專案根目錄執行。

返回值：
    若檢查失敗，以非 0 exit code 結束並列出問題。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def 是否包含中文(文字: str) -> bool:
    """判斷字串是否含 CJK 字元。

    參數：
        文字: 欲檢查字串。

    返回值：
        True 表示含中文。
    """
    return any("\u4e00" <= 字元 <= "\u9fff" for 字元 in 文字)


def 檢查檔案(路徑: Path) -> list[str]:
    """檢查單一 Python 檔案。

    參數：
        路徑: Python 檔路徑。

    返回值：
        問題描述清單。
    """
    問題清單: list[str] = []
    樹 = ast.parse(路徑.read_text(encoding="utf-8"), filename=str(路徑))
    if ast.get_docstring(樹) is None:
        問題清單.append(f"{路徑}: module 缺少 docstring")
    for 節點 in ast.walk(樹):
        if isinstance(節點, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            名稱 = 節點.name
            if 名稱 in {"__init__", "__repr__"}:
                pass
            elif not 是否包含中文(名稱):
                問題清單.append(f"{路徑}:{節點.lineno} `{名稱}` 應使用繁中專案自有名稱")
            if ast.get_docstring(節點) is None:
                問題清單.append(f"{路徑}:{節點.lineno} `{名稱}` 缺少 docstring")
    return 問題清單


def 執行檢查() -> int:
    """執行完整檢查。

    參數：無。
    返回值：process exit code。
    """
    根目錄 = Path(__file__).resolve().parents[1] / "繁中代理"
    問題清單: list[str] = []
    for 路徑 in 根目錄.rglob("*.py"):
        問題清單.extend(檢查檔案(路徑))
    if 問題清單:
        print("\n".join(問題清單))
        return 1
    print("繁中文檔與命名檢查通過")
    return 0


if __name__ == "__main__":
    raise SystemExit(執行檢查())
