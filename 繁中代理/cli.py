"""CLI gateway adapter。

功能：
    MVP 階段提供 terminal/CLI 型入口，把使用者訊息送進 AgentRuntime。未來可
    依相同邊界新增 Telegram、Discord 或 API server gateway adapter。

使用方式：
    python3 -m 繁中代理.cli --session demo "請讀取 README"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .代理執行階段 import 代理執行階段
from .工作階段庫 import 工作階段庫
from .模型供應商 import 建立模型供應商


def 建立參數解析器() -> argparse.ArgumentParser:
    """建立 CLI 參數解析器。

    參數：無。
    返回值：ArgumentParser。
    """
    解析器 = argparse.ArgumentParser(description="Hermes-style Traditional Chinese CLI Agent")
    解析器.add_argument("message", nargs="?", help="使用者訊息")
    解析器.add_argument("--session", default=None, help="工作階段識別碼")
    解析器.add_argument("--db", default=str(Path.home() / ".testagent2" / "sessions.sqlite3"), help="SQLite DB 路徑")
    解析器.add_argument("--workdir", default=os.getcwd(), help="工作目錄")
    解析器.add_argument("--model", default=os.getenv("AIAGENT_MODEL", "gemini-2.5-flash-lite"), help="模型名稱")
    解析器.add_argument("--mode", default=os.getenv("AIAGENT_MODEL_MODE", "gemini"), choices=["fake", "gemini"], help="模型模式")
    解析器.add_argument("--max-iters", type=int, default=8, help="最大 tool-loop 迭代次數")
    return 解析器


def 執行主程式() -> None:
    """執行 CLI gateway。

    參數：無。
    返回值：None；會把最終回答印到 stdout。
    """
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args()
    if not 參數.message:
        解析器.error("請提供 message")
    工作階段庫物件 = 工作階段庫(參數.db)
    模型供應商物件 = 建立模型供應商(參數.mode, 參數.model)
    執行階段 = 代理執行階段(
        工作階段庫物件=工作階段庫物件,
        模型供應商物件=模型供應商物件,
        模型名稱=參數.model,
        供應商名稱="fake" if 參數.mode == "fake" else "gemini-adc",
        工作目錄=參數.workdir,
        最大迭代次數=參數.max_iters,
    )
    結果 = 執行階段.執行使用者訊息(參數.message, 工作階段識別碼=參數.session)
    print(結果.最終回答)
    print(f"\n[session={結果.工作階段識別碼} model_calls={結果.模型呼叫次數} tool_calls={結果.工具呼叫次數} compressed={結果.是否已壓縮}]")


if __name__ == "__main__":
    執行主程式()
