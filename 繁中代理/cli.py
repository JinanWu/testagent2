"""CLI gateway adapter。

功能：
    提供 Hermes-style terminal/CLI 入口。支援一次性 prompt、互動 REPL、常用
    slash commands，以及 session list/browse/rename/export/stats 管理子命令。

使用方式：
    python3 -m 繁中代理.cli --session demo "請讀取 README"
    python3 -m 繁中代理.cli
    python3 -m 繁中代理.cli sessions list
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any

from .代理執行階段 import 代理執行階段
from .工作階段庫 import 工作階段庫
from .模型供應商 import 建立模型供應商
from .輔助壓縮摘要 import 解析摘要失敗是否中止


預設資料庫路徑 = str(Path.home() / ".testagent2" / "sessions.sqlite3")


def 建立Sessions參數解析器() -> argparse.ArgumentParser:
    """建立 sessions 管理子命令 parser。

    參數：無。
    返回值：ArgumentParser。專門解析 `sessions ...` 命令，避免與一般 message 衝突。
    """
    解析器 = argparse.ArgumentParser(prog="cli.py sessions", description="管理 session history")
    解析器.add_argument("--db", default=預設資料庫路徑, help="SQLite DB 路徑")
    解析器.add_argument("--include-archived", action="store_true", help="包含 archived sessions")
    解析器.add_argument("--source", default=None, help="依來源平台篩選")
    解析器.add_argument("--user-id", default=None, help="依使用者識別碼篩選")
    sessions子命令 = 解析器.add_subparsers(dest="sessions_command", required=True)

    list解析器 = sessions子命令.add_parser("list", help="列出近期 sessions")
    list解析器.add_argument("--limit", type=int, default=20, help="最多列出幾筆")
    list解析器.add_argument("--json", action="store_true", help="輸出 JSON")

    browse解析器 = sessions子命令.add_parser("browse", help="瀏覽近期 sessions 與預覽")
    browse解析器.add_argument("--limit", type=int, default=10, help="最多列出幾筆")
    browse解析器.add_argument("--json", action="store_true", help="輸出 JSON")

    rename解析器 = sessions子命令.add_parser("rename", help="重新命名 session title")
    rename解析器.add_argument("session_id", help="session id")
    rename解析器.add_argument("title", help="新的 session title")

    export解析器 = sessions子命令.add_parser("export", help="匯出 sessions 成 JSONL")
    export解析器.add_argument("output", help="輸出 JSONL 檔案路徑")
    export解析器.add_argument("--limit", type=int, default=1000, help="最多匯出幾個 logical sessions")

    stats解析器 = sessions子命令.add_parser("stats", help="顯示 session store 統計")
    stats解析器.add_argument("--json", action="store_true", help="輸出 JSON")
    return 解析器


def 建立參數解析器() -> argparse.ArgumentParser:
    """建立一般 agent CLI 參數解析器。

    參數：無。
    返回值：ArgumentParser。解析一次性 prompt 與互動 REPL 參數。
    """
    解析器 = argparse.ArgumentParser(description="Hermes-style Traditional Chinese CLI Agent")
    解析器.add_argument("message", nargs="?", help="使用者訊息；省略時進入互動 REPL")
    解析器.add_argument("-q", "--query", default=None, help="一次性使用者訊息；等同 Hermes chat -q")
    解析器.add_argument("--session", default=None, help="工作階段識別碼")
    解析器.add_argument("-r", "--resume", default=None, help="依 session id 或 title resume 工作階段")
    解析器.add_argument("-c", "--continue", dest="continue_session", nargs="?", const="__latest__", default=None, help="resume 最近 session，或依名稱/title resume")
    解析器.add_argument("--db", default=預設資料庫路徑, help="SQLite DB 路徑")
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


def 解析模型設定(參數: argparse.Namespace, 解析器: argparse.ArgumentParser) -> dict[str, Any]:
    """解析 CLI 傳入的模型設定 JSON。

    參數：
        參數: argparse 解析後的 namespace。
        解析器: 目前 parser，用於回報格式錯誤。

    返回值：dict[str, Any]。有效的模型設定快照。
    """
    try:
        return json.loads(參數.model_config_json) if 參數.model_config_json else {"mode": 參數.mode}
    except json.JSONDecodeError as 錯誤:
        解析器.error(f"--model-config-json 不是有效 JSON：{錯誤}")
    return {"mode": 參數.mode}


def 建立執行階段(參數: argparse.Namespace, 工作階段庫物件: 工作階段庫, 解析器: argparse.ArgumentParser) -> 代理執行階段:
    """依 CLI 參數建立 AgentRuntime。

    參數：
        參數: argparse namespace。
        工作階段庫物件: 已開啟的 session store。
        解析器: 用於解析模型設定錯誤。

    返回值：代理執行階段。呼叫者可重複用於同一個 REPL process。
    """
    模型設定 = 解析模型設定(參數, 解析器)
    模型供應商物件 = 建立模型供應商(參數.mode, 參數.model)
    return 代理執行階段(
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


def 印出JSON(資料: Any) -> None:
    """以固定格式印出 JSON。

    參數：
        資料: 可 JSON 序列化資料。

    返回值：None。資料會輸出到 stdout。
    """
    print(json.dumps(資料, ensure_ascii=False, indent=2))


def 格式化時間戳(數值: Any) -> str:
    """把 Unix timestamp 轉成簡短本地時間字串。

    參數：
        數值: timestamp 或 None。

    返回值：str。無法解析時回傳空字串。
    """
    if not 數值:
        return ""
    try:
        from datetime import datetime
        return datetime.fromtimestamp(float(數值)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def 取訊息摘要(訊息: dict[str, Any], 長度: int = 70) -> str:
    """取得一則 message 的單行摘要。

    參數：
        訊息: OpenAI-compatible message dict。
        長度: 最多保留字元數。

    返回值：str。移除換行後的簡短內容。
    """
    文字 = str(訊息.get("content") or "").replace("\n", " ").strip()
    if len(文字) > 長度:
        return 文字[: 長度 - 1] + "…"
    return 文字


def 產生新工作階段識別碼() -> str:
    """產生 Hermes-like 簡短 session id。"""
    from datetime import datetime
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def 解析工作階段參照(工作階段庫物件: 工作階段庫, 參照: str | None, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> str | None:
    """把 session id、title 或 latest 指示解析成可 resume 的 session id。

    參數：
        工作階段庫物件: session store。
        參照: 使用者輸入的 session id/title；None 或 __latest__ 表示最近 session。
        include_archived/source/user_id: 與 session list 相同的篩選條件。

    返回值：可 resume 的 session id；找不到時回傳 None。
    """
    if not 參照 or 參照 == "__latest__":
        sessions = 工作階段庫物件.列出工作階段(limit=1, include_archived=include_archived, source=source, user_id=user_id)
        return str(sessions[0]["id"]) if sessions else None
    if 工作階段庫物件.讀取工作階段(參照):
        return 工作階段庫物件.解析Resume工作階段(參照)
    sessions = 工作階段庫物件.列出工作階段(limit=200, include_archived=include_archived, source=source, user_id=user_id)
    for session in sessions:
        if str(session.get("title") or "") == 參照:
            return 工作階段庫物件.解析Resume工作階段(str(session["id"]))
    lowered = 參照.lower()
    for session in sessions:
        if lowered in str(session.get("title") or "").lower() or lowered in str(session.get("id") or "").lower():
            return 工作階段庫物件.解析Resume工作階段(str(session["id"]))
    return None


def 印出工作階段表格(工作階段清單: list[dict[str, Any]], 工作階段庫物件: 工作階段庫 | None = None, 顯示預覽: bool = False) -> None:
    """以文字表格列出 sessions。

    參數：
        工作階段清單: session metadata 清單。
        工作階段庫物件: 可選 session store；顯示預覽時用來讀取訊息。
        顯示預覽: 是否額外印出最近訊息摘要。

    返回值：None。表格會輸出到 stdout。
    """
    if not 工作階段清單:
        print("沒有 session。")
        return
    print("updated           messages tools cost       session/title")
    print("----------------  -------- ----- ---------- ----------------")
    for session in 工作階段清單:
        成本 = session.get("estimated_cost_usd")
        成本文字 = f"${成本:.6f}" if isinstance(成本, (int, float)) else "-"
        標題 = session.get("title") or session.get("id")
        archived = " [archived]" if session.get("archived") else ""
        print(f"{格式化時間戳(session.get('updated_at')):<16} {int(session.get('message_count') or 0):>8} {int(session.get('tool_call_count') or 0):>5} {成本文字:<10} {session.get('id')}  {標題}{archived}")
        if 顯示預覽 and 工作階段庫物件:
            try:
                訊息清單 = 工作階段庫物件.讀取訊息(str(session.get("id")))
            except Exception:
                訊息清單 = []
            for 訊息 in 訊息清單[-2:]:
                print(f"  {訊息.get('role', '?'):<9} {取訊息摘要(訊息)}")


def 執行Sessions子命令(參數: argparse.Namespace) -> None:
    """執行 `testagent2 sessions ...` 管理子命令。

    參數：
        參數: argparse namespace，包含 sessions_command。

    返回值：None。結果會輸出到 stdout 或寫入指定檔案。
    """
    工作階段庫物件 = 工作階段庫(參數.db)
    if 參數.sessions_command == "list":
        sessions = 工作階段庫物件.列出工作階段(limit=參數.limit, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        if 參數.json:
            印出JSON({"sessions": sessions, "total_count": len(sessions)})
        else:
            印出工作階段表格(sessions)
        return
    if 參數.sessions_command == "browse":
        sessions = 工作階段庫物件.列出工作階段(limit=參數.limit, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        if 參數.json:
            印出JSON({"sessions": sessions, "total_count": len(sessions)})
        else:
            印出工作階段表格(sessions, 工作階段庫物件, 顯示預覽=True)
        return
    if 參數.sessions_command == "rename":
        工作階段庫物件.重新命名工作階段(參數.session_id, 參數.title)
        印出JSON({"session_id": 參數.session_id, "title": 參數.title, "renamed": True})
        return
    if 參數.sessions_command == "export":
        結果 = 工作階段庫物件.匯出工作階段JSONL(參數.output, limit=參數.limit, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        印出JSON(結果)
        return
    if 參數.sessions_command == "stats":
        統計 = 工作階段庫物件.統計工作階段(include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        if 參數.json:
            印出JSON(統計)
        else:
            for key, value in 統計.items():
                print(f"{key}: {value}")
        return
    raise SystemExit(f"未知 sessions 子命令：{參數.sessions_command}")


def 讀取多行輸入(第一行: str) -> str:
    """讀取 REPL 多行輸入。

    參數：
        第一行: 已讀到的第一行文字。

    返回值：str。若使用反斜線續行或三引號區塊，會合併後續輸入。
    """
    if 第一行.startswith('"""') or 第一行.startswith("'''"):
        結束符 = 第一行[:3]
        內容 = [第一行[3:]]
        while True:
            if 內容[-1].endswith(結束符):
                內容[-1] = 內容[-1][: -3]
                break
            內容.append(input("... "))
        return "\n".join(內容)
    行清單 = [第一行]
    while 行清單[-1].endswith("\\"):
        行清單[-1] = 行清單[-1][:-1]
        行清單.append(input("... "))
    return "\n".join(行清單)


class 互動CLI:
    """Hermes-style 最小互動 REPL。"""

    def __init__(self, 參數: argparse.Namespace, 解析器: argparse.ArgumentParser) -> None:
        """初始化互動 CLI 狀態。

        參數：
            參數: argparse namespace。
            解析器: 建立 runtime 時需要的 parser。

        返回值：None。會開啟 session store 並建立 AgentRuntime。
        """
        self.參數 = 參數
        self.解析器 = 解析器
        self.工作階段庫物件 = 工作階段庫(參數.db)
        self.執行階段 = 建立執行階段(參數, self.工作階段庫物件, 解析器)
        self.目前工作階段識別碼 = 參數.session
        self.上一個使用者訊息: str | None = None

    def 執行(self) -> None:
        """進入互動輸入迴圈。

        參數：無。
        返回值：None。直到使用者輸入 /exit、/quit 或 EOF。
        """
        self.印出Banner()
        while True:
            try:
                提示 = f"testagent2[{self.目前工作階段識別碼 or 'new'}]> "
                原始輸入 = input(提示)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print("\n已中斷目前輸入；輸入 /exit 離開。")
                continue
            內容 = 讀取多行輸入(原始輸入).strip()
            if not 內容:
                continue
            if 內容.startswith("/"):
                if not self.處理Slash命令(內容):
                    break
                continue
            self.送出使用者訊息(內容)

    def 印出Banner(self) -> None:
        """印出 REPL 啟動資訊。

        參數：無。
        返回值：None。輸出目前模型、DB、工作目錄與常用提示。
        """
        print("testagent2 Hermes-style REPL")
        print(f"model={self.參數.model} mode={self.參數.mode} db={self.參數.db}")
        print(f"workdir={self.參數.workdir}")
        print("輸入 /help 查看命令；多行可用行尾 \\ 或三引號。")

    def 送出使用者訊息(self, 訊息: str) -> None:
        """把使用者訊息送進 AgentRuntime 並更新 REPL session 狀態。

        參數：
            訊息: 使用者輸入文字。

        返回值：None。最終回答與執行摘要會輸出到 stdout。
        """
        結果 = self.執行階段.執行使用者訊息(訊息, 工作階段識別碼=self.目前工作階段識別碼)
        self.目前工作階段識別碼 = 結果.工作階段識別碼
        self.上一個使用者訊息 = 訊息
        print(結果.最終回答)
        print(f"[session={結果.工作階段識別碼} model_calls={結果.模型呼叫次數} tool_calls={結果.工具呼叫次數} compressed={結果.是否已壓縮}]")

    def 處理Slash命令(self, 命令: str) -> bool:
        """處理 REPL slash command。

        參數：
            命令: 以 `/` 開頭的使用者命令。

        返回值：bool。True 表示繼續 REPL；False 表示離開。
        """
        try:
            parts = shlex.split(命令)
        except ValueError as 錯誤:
            print(f"命令解析失敗：{錯誤}")
            return True
        if not parts:
            return True
        名稱 = parts[0].lower()
        參數列 = parts[1:]
        if 名稱 in {"/exit", "/quit", "/q"}:
            return False
        if 名稱 == "/help":
            self.印出Help()
        elif 名稱 == "/new":
            self.目前工作階段識別碼 = 參數列[0] if 參數列 else None
            print(f"已切換到新 session：{self.目前工作階段識別碼 or '自動產生'}")
        elif 名稱 == "/resume":
            self.命令Resume(參數列)
        elif 名稱 == "/sessions":
            self.命令Sessions(參數列)
        elif 名稱 == "/history":
            self.命令History()
        elif 名稱 == "/retry":
            self.命令Retry()
        elif 名稱 == "/undo":
            self.命令Undo()
        elif 名稱 == "/model":
            self.命令Model(參數列)
        elif 名稱 == "/tools":
            self.命令Tools()
        elif 名稱 == "/skills":
            self.命令Skills()
        elif 名稱 == "/status":
            self.命令Status()
        else:
            print(f"未知命令：{名稱}。輸入 /help 查看可用命令。")
        return True

    def 印出Help(self) -> None:
        """印出可用 slash commands。

        參數：無。
        返回值：None。命令列表會輸出到 stdout。
        """
        print("可用命令：")
        print("  /help                 顯示說明")
        print("  /exit                 離開 REPL")
        print("  /new [session_id]     開始新 session")
        print("  /resume <session_id>  resume 指定 session（會導向 compression tip）")
        print("  /sessions [N]         列出近期 sessions")
        print("  /history              顯示目前 session 訊息")
        print("  /retry                rewind 並重送上一個 user turn")
        print("  /undo                 soft-delete 最後一個 user turn 起的訊息")
        print("  /model [name]         顯示或切換目前模型")
        print("  /tools                列出工具與 handler 狀態")
        print("  /skills               列出內建 Hermes skills")
        print("  /status               顯示目前 REPL 狀態")

    def 命令Resume(self, 參數列: list[str]) -> None:
        """處理 /resume 命令。

        參數：
            參數列: shlex 解析後的命令參數。

        返回值：None。成功時更新目前 session id。
        """
        if not 參數列:
            print("用法：/resume <session_id>")
            return
        原始 = 參數列[0]
        if not self.工作階段庫物件.讀取工作階段(原始):
            print(f"找不到 session：{原始}")
            return
        self.目前工作階段識別碼 = self.工作階段庫物件.解析Resume工作階段(原始)
        print(f"已 resume：{self.目前工作階段識別碼}")

    def 命令Sessions(self, 參數列: list[str]) -> None:
        """處理 /sessions 命令。

        參數：
            參數列: 可選 limit。

        返回值：None。近期 session 表格會輸出到 stdout。
        """
        try:
            limit = int(參數列[0]) if 參數列 else 10
        except ValueError:
            limit = 10
        sessions = self.工作階段庫物件.列出工作階段(limit=limit, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
        印出工作階段表格(sessions, self.工作階段庫物件, 顯示預覽=True)

    def 命令History(self) -> None:
        """處理 /history 命令。

        參數：無。
        返回值：None。印出目前 session active messages。
        """
        if not self.目前工作階段識別碼:
            print("尚未有目前 session。")
            return
        訊息清單 = self.工作階段庫物件.讀取訊息(self.目前工作階段識別碼)
        if not 訊息清單:
            print("目前 session 沒有訊息。")
            return
        for i, 訊息 in enumerate(訊息清單, start=1):
            print(f"{i:>3}. {訊息.get('role', '?'):<9} {取訊息摘要(訊息, 長度=120)}")

    def 取得最後User訊息列(self) -> dict[str, Any] | None:
        """讀取目前 session 最後一則 active user message row。

        參數：無。
        返回值：dict | None。包含 id 與 content；沒有目前 session 或 user row 時回傳 None。
        """
        if not self.目前工作階段識別碼:
            return None
        with self.工作階段庫物件._鎖:
            row = self.工作階段庫物件.連線.execute(
                "SELECT id, content FROM messages WHERE session_id=? AND active=1 AND role='user' ORDER BY id DESC LIMIT 1",
                (self.目前工作階段識別碼,),
            ).fetchone()
        return dict(row) if row else None

    def 命令Retry(self) -> None:
        """處理 /retry 命令。

        參數：無。
        返回值：None。會 soft-delete 最後 user turn 後重送同一段文字。
        """
        row = self.取得最後User訊息列()
        訊息 = (row or {}).get("content") or self.上一個使用者訊息
        if not row or not 訊息:
            print("沒有可 retry 的 user turn。")
            return
        self.工作階段庫物件.rewind到訊息(self.目前工作階段識別碼, int(row["id"]))
        self.送出使用者訊息(str(訊息))

    def 命令Undo(self) -> None:
        """處理 /undo 命令。

        參數：無。
        返回值：None。會從最後 user turn 起 soft-delete active messages。
        """
        row = self.取得最後User訊息列()
        if not row:
            print("沒有可 undo 的 user turn。")
            return
        結果 = self.工作階段庫物件.rewind到訊息(self.目前工作階段識別碼, int(row["id"]))
        print(f"已 undo：rewound_count={結果['rewound_count']} new_head_id={結果['new_head_id']}")

    def 命令Model(self, 參數列: list[str]) -> None:
        """處理 /model 命令。

        參數：
            參數列: 空白表示顯示目前模型；第一個參數表示新模型名稱。

        返回值：None。切換模型時會重建 runtime。
        """
        if not 參數列:
            print(f"model={self.參數.model} mode={self.參數.mode}")
            return
        self.參數.model = 參數列[0]
        self.執行階段 = 建立執行階段(self.參數, self.工作階段庫物件, self.解析器)
        print(f"已切換模型：{self.參數.model}")

    def 命令Tools(self) -> None:
        """處理 /tools 命令。

        參數：無。
        返回值：None。列出目前 runtime 的工具名稱。
        """
        for 名稱, 工具 in sorted(self.執行階段.工具登錄器物件.工具表.items()):
            handler = getattr(工具.處理函數, "__name__", type(工具.處理函數).__name__)
            狀態 = "staged" if handler == "處理未啟用工具" else "enabled"
            print(f"{名稱:<24} {狀態}")

    def 命令Skills(self) -> None:
        """處理 /skills 命令。

        參數：無。
        返回值：None。列出 assets/hermes_skills 內的技能名稱。
        """
        payload = json.loads(self.執行階段.工具登錄器物件.呼叫工具("skills_list", {}))
        技能清單 = payload.get("result", {}).get("skills", []) if payload.get("success") else []
        if not 技能清單:
            print("沒有找到 skills。")
            return
        for 技能 in 技能清單[:100]:
            print(f"{技能['name']}")
        print(f"total={payload.get('result', {}).get('total_count', len(技能清單))}")

    def 命令Status(self) -> None:
        """處理 /status 命令。

        參數：無。
        返回值：None。印出目前 REPL/session 狀態。
        """
        session = self.工作階段庫物件.讀取工作階段(self.目前工作階段識別碼) if self.目前工作階段識別碼 else None
        print(f"session={self.目前工作階段識別碼 or 'new'}")
        print(f"model={self.參數.model} mode={self.參數.mode} source={self.參數.source} user_id={self.參數.user_id or ''}")
        print(f"db={self.參數.db}")
        print(f"workdir={self.參數.workdir}")
        if session:
            print(f"messages={session.get('message_count')} tool_calls={session.get('tool_call_count')} api_calls={session.get('api_call_count')} cost={session.get('estimated_cost_usd')}")


def 執行一次性操作(參數: argparse.Namespace, 解析器: argparse.ArgumentParser) -> bool:
    """執行不需要進入 agent loop 的 CLI 操作。

    參數：
        參數: argparse namespace。
        解析器: parser，用於必要參數錯誤。

    返回值：bool。True 表示已處理並可結束程式；False 表示應進入 chat/REPL。
    """
    工作階段庫物件 = 工作階段庫(參數.db)
    if 參數.resume or 參數.continue_session:
        參照 = 參數.resume or 參數.continue_session
        解析後 = 解析工作階段參照(工作階段庫物件, 參照, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        if 解析後:
            參數.session = 解析後
        elif 參數.resume:
            解析器.error(f"找不到可 resume 的 session：{參照}")
    if 參數.archive_session:
        工作階段庫物件.封存工作階段(參數.archive_session)
        印出JSON({"session_id": 參數.archive_session, "archived": True})
        return True
    if 參數.unarchive_session:
        工作階段庫物件.取消封存工作階段(參數.unarchive_session)
        印出JSON({"session_id": 參數.unarchive_session, "archived": False})
        return True
    if 參數.session_search:
        結果 = 工作階段庫物件.搜尋工作階段(參數.session_search, limit=5, window=5, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        印出JSON({"matches": 結果, "total_count": len(結果)})
        return True
    if 參數.rewind_to_message_id is not None:
        if not 參數.session:
            解析器.error("--rewind-to-message-id 需要搭配 --session")
        執行階段 = 建立執行階段(參數, 工作階段庫物件, 解析器)
        結果 = 執行階段.rewind到訊息(參數.session, 參數.rewind_to_message_id)
        印出JSON(結果)
        return True
    return False


def 執行單次訊息(參數: argparse.Namespace, 解析器: argparse.ArgumentParser) -> None:
    """執行一次性使用者訊息。

    參數：
        參數: argparse namespace；必須包含 message。
        解析器: parser，用於模型設定錯誤。

    返回值：None。最終回答會輸出到 stdout。
    """
    工作階段庫物件 = 工作階段庫(參數.db)
    執行階段 = 建立執行階段(參數, 工作階段庫物件, 解析器)
    訊息 = 參數.query or 參數.message
    結果 = 執行階段.執行使用者訊息(訊息, 工作階段識別碼=參數.session)
    print(結果.最終回答)
    print(f"\n[session={結果.工作階段識別碼} model_calls={結果.模型呼叫次數} tool_calls={結果.工具呼叫次數} compressed={結果.是否已壓縮}]")


def 執行主程式() -> None:
    """執行 CLI gateway。

    參數：無。
    返回值：None。依參數執行 sessions 子命令、一次性 prompt 或互動 REPL。
    """
    if len(sys.argv) > 1 and sys.argv[1] == "sessions":
        sessions解析器 = 建立Sessions參數解析器()
        參數 = sessions解析器.parse_args(sys.argv[2:])
        執行Sessions子命令(參數)
        return
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args()
    if 執行一次性操作(參數, 解析器):
        return
    if 參數.query or 參數.message:
        執行單次訊息(參數, 解析器)
        return
    互動CLI(參數, 解析器).執行()


if __name__ == "__main__":
    執行主程式()
