"""檢查專案自有 Python 物件是否具備文檔字串與繁中命名。

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

允許英文名稱 = {
    # Python / dunder / 慣例名稱
    "__init__",
    "__repr__",
    "self",
    "cls",
    "args",
    "kwargs",
    "e",
    "ex",
    "exc",
    "err",
    "_",
    "i",
    "j",
    "k",
    "n",
    # typing / import alias / 外部套件名稱
    "Any",
    "Iterator",
    "Path",
    "_datetime",
    # 外部 API / OpenAI-compatible / Gemini / session_search 契約與 JSON 欄位
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "tool_name",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "platform_message_id",
    "message_id",
    "user_id",
    "source",
    "model",
    "model_config",
    "parent_session_id",
    "billing_provider",
    "cwd",
    "include_ancestors",
    "include_children",
    "include_archived",
    "limit",
    "window",
    "bookend",
    "around_message_id",
    # 使用者 / auth / 權限資料模型的外部欄位與 CLI/JSON 契約
    "username",
    "display_name",
    "roles",
    "enabled_tools",
    "enabled_skills",
    "skill_roots",
    "allowed_workdirs",
    "memory_home",
    "is_admin",
    "disabled",
    "password",
    "salt",
    "token",
    "auth_file",
    "db_path",
    "expires_at",
    "prompt",
    # SQLite / CLI 輸出與小範圍迴圈慣用暫名
    "conn",
    "tip",
    "where",
    "result",
    "sessions",
    "session",
    "session_stats",
    "active_messages",
    "inactive_messages",
    "db_size",
    "handle",
    "sid",
    "messages",
    "lowered",
    "archived",
    "row",
    "payload",
    "parts",
    "matches",
    "handler",
    "match",
    "key",
    "value",
    "_logger",
}

允許英文前綴 = (
    "__",
)


def 是否包含中文(文字: str) -> bool:
    """判斷字串是否含 CJK 字元。

    參數：
        文字: 欲檢查字串。

    返回值：
        True 表示含中文。
    """
    return any("\u4e00" <= 字元 <= "\u9fff" for 字元 in 文字)


def 是否允許英文名稱(名稱: str) -> bool:
    """判斷英文名稱是否屬於外部契約、慣例或 allowlist。

    參數：
        名稱: AST 中讀到的 identifier。

    返回值：
        True 表示此英文 identifier 可保留。
    """
    if 名稱 in 允許英文名稱:
        return True
    if 名稱.isupper():
        return True
    return any(名稱.startswith(前綴) for 前綴 in 允許英文前綴)


def 記錄英文命名問題(問題清單: list[str], 路徑: Path, 行號: int, 名稱: str, 種類: str) -> None:
    """把不符合繁中命名的 identifier 加入問題清單。

    參數：
        問題清單: 累積問題的清單。
        路徑: Python 檔路徑。
        行號: 問題所在行號。
        名稱: identifier 名稱。
        種類: 問題種類，供輸出辨識。

    返回值：None。
    """
    if 是否包含中文(名稱) or 是否允許英文名稱(名稱):
        return
    問題清單.append(f"{路徑}:{行號} `{名稱}` 應使用繁中專案自有名稱（{種類}）")


def 檢查檔案(路徑: Path) -> list[str]:
    """檢查單一 Python 檔案。

    參數：
        路徑: Python 檔路徑。

    返回值：
        問題描述清單。
    """
    問題清單: list[str] = []
    樹 = ast.parse(路徑.read_text(encoding="utf-8"), filename=str(路徑))
    外部欄位節點: set[int] = set()
    if 路徑.parts[-4:] == ("繁中代理", "發布介面", "治理", "觀測契約.py"):
        for 類別 in (節點 for 節點 in 樹.body if isinstance(節點, ast.ClassDef)):
            for 陳述 in 類別.body:
                if isinstance(陳述, ast.AnnAssign) and isinstance(陳述.target, ast.Name):
                    外部欄位節點.add(id(陳述.target))
    if ast.get_docstring(樹) is None:
        問題清單.append(f"{路徑}: module 缺少 docstring")
    for 節點 in ast.walk(樹):
        if isinstance(節點, (ast.FunctionDef, ast.AsyncFunctionDef)):
            名稱 = 節點.name
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 名稱, "function")
            if ast.get_docstring(節點) is None:
                問題清單.append(f"{路徑}:{節點.lineno} `{名稱}` 缺少 docstring")
            for 參數 in [*節點.args.posonlyargs, *節點.args.args, *節點.args.kwonlyargs]:
                記錄英文命名問題(問題清單, 路徑, 參數.lineno, 參數.arg, "argument")
            if 節點.args.vararg:
                記錄英文命名問題(問題清單, 路徑, 節點.args.vararg.lineno, 節點.args.vararg.arg, "vararg")
            if 節點.args.kwarg:
                記錄英文命名問題(問題清單, 路徑, 節點.args.kwarg.lineno, 節點.args.kwarg.arg, "kwarg")
        elif isinstance(節點, ast.ClassDef):
            名稱 = 節點.name
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 名稱, "class")
            if ast.get_docstring(節點) is None:
                問題清單.append(f"{路徑}:{節點.lineno} `{名稱}` 缺少 docstring")
        elif (isinstance(節點, ast.Name) and isinstance(節點.ctx, ast.Store)
              and id(節點) not in 外部欄位節點):
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.id, "variable")
        elif isinstance(節點, ast.ExceptHandler) and 節點.name:
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.name, "exception alias")
        elif isinstance(節點, ast.alias) and 節點.asname:
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.asname, "import alias")
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
