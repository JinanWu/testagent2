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
import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

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


@dataclass(frozen=True)
class 問題:
    """單一繁中規範finding的結構化證據。"""

    路徑: PurePath
    行號: int | None
    規則: str
    訊息: str


class 檢查設定錯誤(RuntimeError):
    """表示scan root或baseline無法可信使用。"""


def 正規化相對路徑(根目錄: PurePath, 路徑: PurePath) -> str:
    """建立NFC與POSIX separator的checkout-relative path。"""
    try:
        相對路徑 = 路徑.relative_to(根目錄)
    except ValueError as exc:
        raise 檢查設定錯誤(f"finding路徑不在scan root內：{路徑}") from exc
    return unicodedata.normalize("NFC", 相對路徑.as_posix())


def canonical排序鍵(item: dict[str, object]) -> tuple[str, int, int, str, str]:
    """以null discriminator建立跨平台穩定排序。"""
    line = item["line"]
    line_value = line if isinstance(line, int) else 0
    return (str(item["path"]), 0 if line is None else 1, line_value, str(item["rule"]), str(item["message"]))


def 建立canonical問題(根目錄: PurePath, 問題清單: list[問題]) -> list[dict[str, object]]:
    """建立排序穩定且無checkout絕對路徑的finding records。"""
    records = [
        {
            "line": item.行號,
            "message": unicodedata.normalize("NFC", item.訊息),
            "path": 正規化相對路徑(根目錄, item.路徑),
            "rule": unicodedata.normalize("NFC", item.規則),
        }
        for item in 問題清單
    ]
    return sorted(records, key=canonical排序鍵)


def 編碼canonical問題(records: list[dict[str, object]]) -> str:
    """以injective deterministic JSON編碼完整finding集合。"""
    return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def 記錄英文命名問題(問題清單: list[問題], 路徑: Path, 行號: int, 名稱: str, 種類: str) -> None:
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
    問題清單.append(問題(路徑, 行號, f"{種類}_name", f"`{名稱}` 應使用繁中專案自有名稱（{種類}）"))


def 檢查檔案(路徑: Path) -> list[問題]:
    """檢查單一 Python 檔案。

    參數：
        路徑: Python 檔路徑。

    返回值：
        問題描述清單。
    """
    問題清單: list[問題] = []
    樹 = ast.parse(路徑.read_text(encoding="utf-8"), filename=str(路徑))
    外部欄位節點: set[int] = set()
    if 路徑.parts[-4:] == ("繁中代理", "發布介面", "治理", "觀測契約.py"):
        for 類別 in (節點 for 節點 in 樹.body if isinstance(節點, ast.ClassDef)):
            for 陳述 in 類別.body:
                if isinstance(陳述, ast.AnnAssign) and isinstance(陳述.target, ast.Name):
                    外部欄位節點.add(id(陳述.target))
    if ast.get_docstring(樹) is None:
        問題清單.append(問題(路徑, None, "module_docstring", "module 缺少 docstring"))
    for 節點 in ast.walk(樹):
        if isinstance(節點, (ast.FunctionDef, ast.AsyncFunctionDef)):
            名稱 = 節點.name
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 名稱, "function")
            if ast.get_docstring(節點) is None:
                問題清單.append(問題(路徑, 節點.lineno, "function_docstring", f"`{名稱}` 缺少 docstring"))
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
                問題清單.append(問題(路徑, 節點.lineno, "class_docstring", f"`{名稱}` 缺少 docstring"))
        elif (isinstance(節點, ast.Name) and isinstance(節點.ctx, ast.Store)
              and id(節點) not in 外部欄位節點):
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.id, "variable")
        elif isinstance(節點, ast.ExceptHandler) and 節點.name:
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.name, "exception alias")
        elif isinstance(節點, ast.alias) and 節點.asname:
            記錄英文命名問題(問題清單, 路徑, 節點.lineno, 節點.asname, "import alias")
    return 問題清單


def 掃描問題(根目錄: Path) -> list[問題]:
    """掃描可信root；root缺失或沒有Python source時fail closed。"""
    if not 根目錄.is_dir():
        raise 檢查設定錯誤(f"繁中checker root不存在或非目錄：{根目錄}")
    路徑清單 = sorted(根目錄.rglob("*.py"), key=lambda path: path.as_posix())
    if not 路徑清單:
        raise 檢查設定錯誤(f"繁中checker root沒有Python source：{根目錄}")
    問題清單: list[問題] = []
    for 路徑 in 路徑清單:
        問題清單.extend(檢查檔案(路徑))
    return 問題清單


def 讀取baseline(路徑: Path) -> list[dict[str, object]]:
    """讀取並驗證owner-approved canonical manifest。"""
    try:
        payload = json.loads(路徑.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise 檢查設定錯誤(f"繁中checker baseline無法讀取：{路徑}") from exc
    if not isinstance(payload, list):
        raise 檢查設定錯誤("繁中checker baseline必須是JSON array")
    欄位 = {"line", "message", "path", "rule"}
    for record in payload:
        if not isinstance(record, dict) or set(record) != 欄位:
            raise 檢查設定錯誤("繁中checker baseline record schema錯誤")
        path, rule, message, line = record["path"], record["rule"], record["message"], record["line"]
        if not all(isinstance(value, str) and value for value in (path, rule, message)):
            raise 檢查設定錯誤("繁中checker baseline文字欄位必須為非空字串")
        if line is not None and (type(line) is not int or line < 1):
            raise 檢查設定錯誤("繁中checker baseline line必須為null或正整數")
        windows_path, posix_path = PureWindowsPath(path), PurePosixPath(path)
        if "\\" in path or windows_path.is_absolute() or windows_path.drive:
            raise 檢查設定錯誤("繁中checker baseline path不得使用Windows或反斜線格式")
        if posix_path.is_absolute() or path != posix_path.as_posix() or any(part in ("", ".", "..") for part in path.split("/")):
            raise 檢查設定錯誤("繁中checker baseline path必須為canonical安全POSIX相對路徑")
        for value in (path, rule, message):
            if value != unicodedata.normalize("NFC", value) or any(unicodedata.category(char).startswith("C") for char in value):
                raise 檢查設定錯誤("繁中checker baseline文字必須為NFC且不含控制字元")
    canonical = sorted(payload, key=canonical排序鍵)
    if payload != canonical:
        raise 檢查設定錯誤("繁中checker baseline未使用canonical排序")
    encoded_records = [編碼canonical問題([record]) for record in canonical]
    if len(encoded_records) != len(set(encoded_records)):
        raise 檢查設定錯誤("繁中checker baseline包含重複record")
    for record in canonical:
        path = PurePath(str(record["path"]))
        if path.is_absolute() or ".." in path.parts:
            raise 檢查設定錯誤("繁中checker baseline path必須為安全相對路徑")
        for field in ("path", "rule", "message"):
            value = str(record[field])
            if value != unicodedata.normalize("NFC", value):
                raise 檢查設定錯誤("繁中checker baseline文字必須為NFC")
    return canonical


def 判定問題集合(根目錄: PurePath, 問題清單: list[問題], baseline路徑: Path) -> int:
    """接受clean或exact owner baseline；任何集合漂移都失敗。"""
    current = 建立canonical問題(根目錄, 問題清單)
    current_encoded = [編碼canonical問題([record]) for record in current]
    if len(current_encoded) != len(set(current_encoded)):
        raise 檢查設定錯誤("繁中checker目前掃描包含重複record")
    baseline = 讀取baseline(baseline路徑)
    if not current:
        print("繁中文檔與命名檢查通過")
        return 0
    if current == baseline:
        fingerprint = hashlib.sha256(編碼canonical問題(current).encode("utf-8")).hexdigest()
        print(f"繁中checker基線通過：既有問題{len(current)}項，新增或變更0項，指紋{fingerprint}")
        return 0
    current_set = {編碼canonical問題([record]) for record in current}
    baseline_set = {編碼canonical問題([record]) for record in baseline}
    added = sorted(current_set - baseline_set)
    removed = sorted(baseline_set - current_set)
    print(f"繁中checker基線漂移：新增{len(added)}項，移除{len(removed)}項")
    for label, records in (("新增", added[:20]), ("移除", removed[:20])):
        for record in records:
            print(f"{label}: {record}")
    if len(added) > 20 or len(removed) > 20:
        print("delta僅顯示各前20項；完整集合請由canonical manifest比較")
    return 1


def 執行檢查() -> int:
    """執行完整檢查；設定錯誤使用exit 2 fail closed。"""
    專案根 = Path(__file__).resolve().parents[1]
    根目錄 = 專案根 / "繁中代理"
    baseline路徑 = 專案根 / "scripts" / "繁中checker-baseline.json"
    try:
        return 判定問題集合(根目錄, 掃描問題(根目錄), baseline路徑)
    except (檢查設定錯誤, OSError, SyntaxError, UnicodeError) as exc:
        print(f"繁中checker設定或掃描失敗：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(執行檢查())
