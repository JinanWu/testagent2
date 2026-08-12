"""測試 Hermes prompt 與 core tool schema 的來源一致性。"""

import ast
import json
from pathlib import Path

import pytest

from 繁中代理 import 工具註冊
from 繁中代理.工具註冊 import 建立預設工具登錄器
from 繁中代理.提示詞常數 import 完成任務指引, 工具使用強制指引, 壓縮摘要前綴


def 靜態求值字串(節點: ast.expr, 已知值: dict[str, str]) -> str:
    """只解析字串literal、名稱、串接與f-string，拒絕其他Python表達式。"""
    if isinstance(節點, ast.Constant) and isinstance(節點.value, str):
        return 節點.value
    if isinstance(節點, ast.Name) and 節點.id in 已知值:
        return 已知值[節點.id]
    if isinstance(節點, ast.BinOp) and isinstance(節點.op, ast.Add):
        return 靜態求值字串(節點.left, 已知值) + 靜態求值字串(節點.right, 已知值)
    if isinstance(節點, ast.JoinedStr):
        片段 = []
        for 子節點 in 節點.values:
            if isinstance(子節點, ast.FormattedValue):
                if 子節點.conversion != -1 or 子節點.format_spec is not None:
                    raise AssertionError("不允許f-string conversion或format spec")
                片段.append(靜態求值字串(子節點.value, 已知值))
            else:
                片段.append(靜態求值字串(子節點, 已知值))
        return "".join(片段)
    raise AssertionError(f"不允許動態求值：{ast.dump(節點, include_attributes=False)}")


def 收集名稱參照(節點: ast.expr) -> set[str]:
    """收集靜態字串expression中的Load名稱，不接受其他scope。"""
    return {
        子節點.id
        for 子節點 in ast.walk(節點)
        if isinstance(子節點, ast.Name) and isinstance(子節點.ctx, ast.Load)
    }


def 讀取靜態字串定義(路徑: Path, 名稱: str) -> str:
    """解析top-level靜態字串definition closure；只讀原始碼，不執行module。"""
    樹 = ast.parse(路徑.read_text(encoding="utf-8"), filename=str(路徑))
    指派表: dict[str, list[tuple[ast.expr, tuple[int, int]]]] = {}
    for 陳述 in 樹.body:
        if not isinstance(陳述, ast.Assign) or len(陳述.targets) != 1:
            continue
        目標 = 陳述.targets[0]
        if isinstance(目標, ast.Name):
            指派表.setdefault(目標.id, []).append((陳述.value, (陳述.lineno, 陳述.col_offset)))

    已知值: dict[str, str] = {}
    解析中: set[str] = set()

    def 解析名稱(待解析名稱: str, 使用位置: tuple[float, float]) -> str:
        指派清單 = 指派表.get(待解析名稱, [])
        if len(指派清單) != 1:
            raise AssertionError(
                f"{路徑}的{待解析名稱}必須恰有一個top-level靜態字串definition，實際{len(指派清單)}個"
            )
        表達式, 指派位置 = 指派清單[0]
        if 指派位置 >= 使用位置:
            raise AssertionError(f"{路徑}的{待解析名稱}在使用後才定義")
        if 待解析名稱 in 已知值:
            return 已知值[待解析名稱]
        if 待解析名稱 in 解析中:
            raise AssertionError(f"{路徑}的{待解析名稱}存在循環dependency")
        解析中.add(待解析名稱)
        try:
            for 參照名稱 in 收集名稱參照(表達式):
                解析名稱(參照名稱, 指派位置)
            值 = 靜態求值字串(表達式, 已知值)
        finally:
            解析中.remove(待解析名稱)
        已知值[待解析名稱] = 值
        return 值

    return 解析名稱(名稱, (float("inf"), float("inf")))


def test_靜態字串reader只接受明確definition_closure(tmp_path):
    """鎖定lexical parity seam，不把任意Python runtime誤稱為可靜態證明。"""
    來源路徑 = tmp_path / "source.py"
    來源路徑.write_text(
        "A = 'Title'\nHEADING = A\nTARGET = f'## {HEADING}' + '!'\n",
        encoding="utf-8",
    )
    assert 讀取靜態字串定義(來源路徑, "TARGET") == "## Title!"

    來源路徑.write_text("A = 'x'; TARGET = A\n", encoding="utf-8")
    assert 讀取靜態字串定義(來源路徑, "TARGET") == "x"

    for 原始碼, 訊息 in (
        ("TARGET = 建立值()\n", "實際0個|不允許動態求值"),
        ("TARGET = 物件.value\n", "實際0個|不允許動態求值"),
        ("TARGET = 清單[0]\n", "實際0個|不允許動態求值"),
        ("A = 'value'\nTARGET = f'{A!r}'\n", "conversion或format spec"),
        ("A = 'value'\nTARGET = f'{A:>3}'\n", "conversion或format spec"),
        ("TARGET = 'first'\nTARGET = 'second'\n", "實際2個"),
        ("TARGET = A\nA = 'later'\n", "在使用後才定義"),
        ("TARGET = A; A = 'later'\n", "在使用後才定義"),
        ("A = B\nB = A\nTARGET = A\n", "循環dependency|在使用後才定義"),
    ):
        來源路徑.write_text(原始碼, encoding="utf-8")
        with pytest.raises(AssertionError, match=訊息):
            讀取靜態字串定義(來源路徑, "TARGET")


def test_prompt_常數_使用_hermes_原文():
    """確認關鍵提示詞常數與Hermes原始碼中的literal文字一致。"""
    Hermes原始碼路徑 = Path("/Users/wujinan/Documents/hermes-agent")
    if not Hermes原始碼路徑.exists():
        pytest.skip("本機沒有Hermes原始碼checkout，略過原文parity測試")
    提示詞路徑 = Hermes原始碼路徑 / "agent" / "prompt_builder.py"
    壓縮器路徑 = Hermes原始碼路徑 / "agent" / "context_compressor.py"

    assert 完成任務指引 == 讀取靜態字串定義(提示詞路徑, "TASK_COMPLETION_GUIDANCE")
    assert 工具使用強制指引 == 讀取靜態字串定義(提示詞路徑, "TOOL_USE_ENFORCEMENT_GUIDANCE")
    assert 壓縮摘要前綴 == 讀取靜態字串定義(壓縮器路徑, "SUMMARY_PREFIX")


def test_core_tool_schema_完整載入_hermes_核心工具():
    """確認本專案載入 Hermes 48 個 core tool schema，並額外載入專案自訂工具。"""
    結構路徑 = Path("assets/hermes_core_tool_schemas.json")
    結構清單 = json.loads(結構路徑.read_text(encoding="utf-8"))
    自訂結構路徑 = Path("assets/hermes_custom_tool_schemas.json")
    自訂結構清單 = json.loads(自訂結構路徑.read_text(encoding="utf-8"))
    登錄器 = 建立預設工具登錄器()
    assert len(結構清單) == 48
    assert len(登錄器.工具表) == len(結構清單) + len(自訂結構清單)
    for 名稱 in ["read_file", "write_file", "patch", "search_files", "terminal", "skill_view", "memory", "session_search", "delegate_task"]:
        assert 名稱 in 登錄器.工具表
    assert "administrative_search" in 登錄器.工具表
    assert 登錄器.工具表["read_file"].說明 == 結構清單[5]["schema"]["description"]


def test_custom_schema_存在但_core_schema_缺失時仍保留內建工具(monkeypatch):
    """確認 core schema 遺失時，不會因自訂工具存在而漏註冊內建工具。"""
    自訂結構清單 = [
        {
            "schema": {
                "name": "administrative_search",
                "description": "管理部搜尋",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    ]

    def 假載入工具結構清單(路徑):
        if 路徑.name == "hermes_custom_tool_schemas.json":
            return 自訂結構清單
        return []

    monkeypatch.setattr(工具註冊, "載入工具結構清單", 假載入工具結構清單)
    登錄器 = 建立預設工具登錄器()

    assert "administrative_search" in 登錄器.工具表
    assert "read_file" in 登錄器.工具表
    assert "terminal" in 登錄器.工具表
    assert 登錄器.工具表["administrative_search"].說明 == "管理部搜尋"
