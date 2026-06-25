"""CLI gateway adapter。

功能：
    MVP 階段提供 terminal/CLI 型入口，把使用者訊息送進 AgentRuntime。未來可
    依相同邊界新增 Telegram、Discord 或 API server gateway adapter。

使用方式：
    python3 -m 繁中代理.cli --session demo "請讀取 README"
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .代理執行階段 import 代理執行階段
from .工作階段庫 import 工作階段庫
from .模型供應商 import 建立模型供應商
from .輔助壓縮摘要 import 解析摘要失敗是否中止


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
    解析器.add_argument("--user-id", default=os.getenv("TESTAGENT2_USER_ID"), help="使用者識別碼，會寫入 sessions.user_id")
    解析器.add_argument("--source", default=os.getenv("TESTAGENT2_SOURCE", "cli"), help="session 來源平台，預設 cli")
    解析器.add_argument("--model-config-json", default=None, help="JSON 格式模型設定，會寫入 sessions.model_config")
    解析器.add_argument("--include-archived", action="store_true", help="session list/search 是否包含 archived sessions")
    解析器.add_argument("--archive-session", default=None, help="封存指定 session，不呼叫模型")
    解析器.add_argument("--unarchive-session", default=None, help="取消封存指定 session，不呼叫模型")
    解析器.add_argument("--session-search", default=None, help="搜尋既有 session history，不呼叫模型")
    解析器.add_argument("--rewind-to-message-id", type=int, default=None, help="soft-delete rewind 到指定 message row id，不呼叫模型")
    解析器.add_argument(
        "--compression-llm",
        default=os.getenv("AIAGENT_COMPRESSION_LLM", "on"),
        choices=["on", "off"],
        help="是否使用 auxiliary LLM 產生 compression summary（off 時只用 deterministic fallback）",
    )
    解析器.add_argument(
        "--compression-model",
        default=os.getenv("AIAGENT_COMPRESSION_MODEL"),
        help="compression 摘要模型；預設 auto 重用 --model",
    )
    解析器.add_argument(
        "--compression-mode",
        default=None,
        choices=["fake", "gemini", "off"],
        help="compression 摘要 provider 模式；預設 auto 重用 --mode",
    )
    解析器.add_argument(
        "--abort-on-summary-failure",
        action="store_true",
        help="摘要模型失敗時中止壓縮（對應 Hermes abort_on_summary_failure）",
    )
    return 解析器


def 執行主程式() -> None:
    """執行 CLI gateway。

    參數：無。
    返回值：None；會把最終回答印到 stdout。
    """
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args()
    if not 參數.message and not 參數.session_search and 參數.rewind_to_message_id is None and not 參數.archive_session and not 參數.unarchive_session:
        解析器.error("請提供 message，或使用 --session-search / --rewind-to-message-id / --archive-session / --unarchive-session")
    工作階段庫物件 = 工作階段庫(參數.db)
    if 參數.archive_session:
        工作階段庫物件.封存工作階段(參數.archive_session)
        print(json.dumps({"session_id": 參數.archive_session, "archived": True}, ensure_ascii=False, indent=2))
        return
    if 參數.unarchive_session:
        工作階段庫物件.取消封存工作階段(參數.unarchive_session)
        print(json.dumps({"session_id": 參數.unarchive_session, "archived": False}, ensure_ascii=False, indent=2))
        return
    if 參數.session_search:
        結果 = 工作階段庫物件.搜尋工作階段(參數.session_search, limit=5, window=5, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        print(json.dumps({"matches": 結果, "total_count": len(結果)}, ensure_ascii=False, indent=2))
        return
    try:
        模型設定 = json.loads(參數.model_config_json) if 參數.model_config_json else {"mode": 參數.mode}
    except json.JSONDecodeError as 錯誤:
        解析器.error(f"--model-config-json 不是有效 JSON：{錯誤}")
    模型供應商物件 = 建立模型供應商(參數.mode, 參數.model)
    執行階段 = 代理執行階段(
        工作階段庫物件=工作階段庫物件,
        模型供應商物件=模型供應商物件,
        模型名稱=參數.model,
        供應商名稱="fake" if 參數.mode == "fake" else "gemini-adc",
        工作目錄=參數.workdir,
        最大迭代次數=參數.max_iters,
        模型模式=參數.mode,
        user_id=參數.user_id,
        source=參數.source,
        model_config=模型設定,
        啟用壓縮摘要=參數.compression_llm == "on",
        摘要失敗是否中止=參數.abort_on_summary_failure or 解析摘要失敗是否中止(),
        壓縮模式=參數.compression_mode,
        壓縮模型=參數.compression_model,
    )
    if 參數.rewind_to_message_id is not None:
        if not 參數.session:
            解析器.error("--rewind-to-message-id 需要搭配 --session")
        結果 = 執行階段.rewind到訊息(參數.session, 參數.rewind_to_message_id)
        print(json.dumps(結果, ensure_ascii=False, indent=2))
        return
    結果 = 執行階段.執行使用者訊息(參數.message, 工作階段識別碼=參數.session)
    print(結果.最終回答)
    print(f"\n[session={結果.工作階段識別碼} model_calls={結果.模型呼叫次數} tool_calls={結果.工具呼叫次數} compressed={結果.是否已壓縮}]")


if __name__ == "__main__":
    執行主程式()
