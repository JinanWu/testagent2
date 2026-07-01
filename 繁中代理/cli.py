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
from .使用者 import (
    使用者上下文,
    使用者庫,
    取得預設記憶根目錄,
    建立預設使用者上下文,
    讀取Auth檔案,
    寫入Auth檔案,
    刪除Auth檔案,
    讀取密碼輸入,
)


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

    search解析器 = sessions子命令.add_parser("search", help="搜尋 session history")
    search解析器.add_argument("query", help="搜尋關鍵字")
    search解析器.add_argument("--limit", type=int, default=5, help="最多列出幾筆")
    search解析器.add_argument("--json", action="store_true", help="輸出 JSON")

    rename解析器 = sessions子命令.add_parser("rename", help="重新命名 session title")
    rename解析器.add_argument("session_id", help="session id")
    rename解析器.add_argument("title", help="新的 session title")

    export解析器 = sessions子命令.add_parser("export", help="匯出 sessions 成 JSONL")
    export解析器.add_argument("output", help="輸出 JSONL 檔案路徑")
    export解析器.add_argument("--limit", type=int, default=1000, help="最多匯出幾個 logical sessions")

    stats解析器 = sessions子命令.add_parser("stats", help="顯示 session store 統計")
    stats解析器.add_argument("--json", action="store_true", help="輸出 JSON")
    return 解析器


def 建立Auth參數解析器() -> argparse.ArgumentParser:
    """建立 auth 子命令 parser。

    參數：無。
    返回值：ArgumentParser。支援 login、logout 與 whoami。
    """
    說明 = """管理 CLI 本機登入狀態

常用流程：
  python3 -m 繁中代理.cli users create alice --password <密碼>
  python3 -m 繁中代理.cli auth login alice
  python3 -m 繁中代理.cli auth whoami
  python3 -m 繁中代理.cli auth logout

環境變數：
  TESTAGENT2_AUTH_FILE      指定本機 token 檔案位置
  TESTAGENT2_REQUIRE_LOGIN  設為 1 時，沒有登入就拒絕執行 agent
"""
    解析器 = argparse.ArgumentParser(prog="python3 -m 繁中代理.cli auth", description=說明, formatter_class=argparse.RawDescriptionHelpFormatter)
    解析器.add_argument("--db", default=預設資料庫路徑, help="SQLite DB 路徑")
    子命令 = 解析器.add_subparsers(dest="auth_command", required=True)
    login解析器 = 子命令.add_parser("login", help="以帳密登入")
    login解析器.add_argument("username", help="登入帳號")
    login解析器.add_argument("--password", default=None, help="密碼；未提供時互動輸入或讀 TESTAGENT2_PASSWORD")
    子命令.add_parser("logout", help="登出並撤銷目前 token")
    子命令.add_parser("whoami", help="顯示目前登入使用者")
    return 解析器


def 建立Users參數解析器() -> argparse.ArgumentParser:
    """建立 users 管理子命令 parser。

    參數：無。
    返回值：ArgumentParser。支援建立、列出、停用與設定權限。
    """
    說明 = """管理本機使用者與權限

常用流程：
  python3 -m 繁中代理.cli users create alice --password <密碼> --workdirs /path/to/repo
  python3 -m 繁中代理.cli users list
  python3 -m 繁中代理.cli users set-tools alice read_file,search_files,terminal
  python3 -m 繁中代理.cli users set-skills alice hermes-agent,verification-and-debugging
  python3 -m 繁中代理.cli users disable alice

權限格式：
  --tools / --skills / set-* items 使用逗號分隔；* 表示全部允許。
  --workdirs / set-workdirs 限制 read_file、write_file、patch、search_files、terminal 的可用目錄。
"""
    解析器 = argparse.ArgumentParser(prog="python3 -m 繁中代理.cli users", description=說明, formatter_class=argparse.RawDescriptionHelpFormatter)
    解析器.add_argument("--db", default=預設資料庫路徑, help="SQLite DB 路徑")
    子命令 = 解析器.add_subparsers(dest="users_command", required=True)
    create解析器 = 子命令.add_parser("create", help="建立使用者")
    create解析器.add_argument("username", help="登入帳號")
    create解析器.add_argument("--password", default=None, help="密碼；未提供時互動輸入或讀 TESTAGENT2_PASSWORD")
    create解析器.add_argument("--display-name", default=None, help="顯示名稱")
    create解析器.add_argument("--roles", default="user", help="逗號分隔角色，例如 user,admin")
    create解析器.add_argument("--tools", default="*", help="逗號分隔工具；* 表示全部")
    create解析器.add_argument("--skills", default="*", help="逗號分隔技能；* 表示全部")
    create解析器.add_argument("--skill-roots", default="", help="逗號分隔技能根目錄")
    create解析器.add_argument("--workdirs", default="", help="逗號分隔允許工作目錄；* 表示全部")
    子命令.add_parser("list", help="列出使用者")
    disable解析器 = 子命令.add_parser("disable", help="停用使用者")
    disable解析器.add_argument("username")
    enable解析器 = 子命令.add_parser("enable", help="啟用使用者")
    enable解析器.add_argument("username")
    for 名稱, 欄位 in [("set-tools", "enabled_tools_json"), ("set-skills", "enabled_skills_json"), ("set-skill-roots", "skill_roots_json"), ("set-workdirs", "allowed_workdirs_json")]:
        子解析器 = 子命令.add_parser(名稱, help=f"更新 {欄位}")
        子解析器.add_argument("username")
        子解析器.add_argument("items", help="逗號分隔項目；* 表示全部")
        子解析器.set_defaults(設定欄位=欄位)
    return 解析器


def 解析逗號清單(文字: str | None) -> list[str]:
    """解析 CLI 逗號分隔清單。

    參數：
        文字: 使用者輸入的逗號分隔字串。

    返回值：
        去空白字串清單。
    """
    return [項目.strip() for 項目 in str(文字 or "").split(",") if 項目.strip()]


def 執行Auth子命令(參數: argparse.Namespace) -> None:
    """執行 auth 子命令。

    參數：
        參數: argparse namespace。

    返回值：None。結果輸出到 stdout。
    """
    使用者庫物件 = 使用者庫(參數.db)
    if 參數.auth_command == "login":
        密碼 = 參數.password if 參數.password is not None else 讀取密碼輸入()
        使用者 = 使用者庫物件.驗證使用者密碼(參數.username, 密碼)
        舊auth資料 = 讀取Auth檔案()
        if 舊auth資料 and 舊auth資料.get("token"):
            使用者庫(舊auth資料.get("db_path") or 參數.db).撤銷登入Token(str(舊auth資料["token"]))
        token = 使用者庫物件.建立登入Token(str(使用者["id"]))
        路徑 = 寫入Auth檔案(str(使用者["username"]), str(使用者["id"]), token, db_path=參數.db)
        印出JSON({"logged_in": True, "username": 使用者["username"], "user_id": 使用者["id"], "auth_file": str(路徑)})
        return
    if 參數.auth_command == "logout":
        auth資料 = 讀取Auth檔案()
        token資料庫路徑 = auth資料.get("db_path") if auth資料 else None
        if auth資料 and auth資料.get("token"):
            使用者庫(token資料庫路徑 or 參數.db).撤銷登入Token(str(auth資料["token"]))
        刪除Auth檔案()
        印出JSON({"logged_out": True})
        return
    if 參數.auth_command == "whoami":
        auth資料 = 讀取Auth檔案()
        if not auth資料 or not auth資料.get("token"):
            印出JSON({"logged_in": False})
            return
        try:
            token資料庫路徑 = auth資料.get("db_path") or 參數.db
            上下文 = 使用者庫(token資料庫路徑).驗證登入Token(str(auth資料["token"]))
        except ValueError:
            印出JSON({"logged_in": False})
            return
        印出JSON({"logged_in": True, "user": 上下文.序列化()})
        return


def 執行Users子命令(參數: argparse.Namespace) -> None:
    """執行 users 子命令。

    參數：
        參數: argparse namespace。

    返回值：None。結果輸出到 stdout。
    """
    使用者庫物件 = 使用者庫(參數.db)
    if 參數.users_command == "create":
        密碼 = 參數.password if 參數.password is not None else 讀取密碼輸入()
        使用者 = 使用者庫物件.建立使用者(
            參數.username,
            password=密碼,
            display_name=參數.display_name,
            roles=解析逗號清單(參數.roles),
            enabled_tools=解析逗號清單(參數.tools),
            enabled_skills=解析逗號清單(參數.skills),
            skill_roots=解析逗號清單(參數.skill_roots),
            allowed_workdirs=解析逗號清單(參數.workdirs),
        )
        印出JSON({"created": True, "user": 使用者})
        return
    if 參數.users_command == "list":
        印出JSON({"users": 使用者庫物件.列出使用者()})
        return
    if 參數.users_command == "disable":
        使用者庫物件.設定使用者停用(參數.username, True)
        印出JSON({"username": 參數.username, "disabled": True})
        return
    if 參數.users_command == "enable":
        使用者庫物件.設定使用者停用(參數.username, False)
        印出JSON({"username": 參數.username, "disabled": False})
        return
    if 參數.users_command.startswith("set-"):
        使用者庫物件.設定權限欄位(參數.username, 參數.設定欄位, 解析逗號清單(參數.items))
        印出JSON({"username": 參數.username, "field": 參數.設定欄位, "updated": True})
        return


def 建立參數解析器() -> argparse.ArgumentParser:
    """建立一般 agent CLI 參數解析器。

    參數：無。
    返回值：ArgumentParser。解析一次性 prompt 與互動 REPL 參數。
    """
    說明 = """Hermes-style Traditional Chinese CLI Agent

常用流程：
  python3 -m 繁中代理.cli --help
  python3 -m 繁中代理.cli users --help
  python3 -m 繁中代理.cli auth --help
  python3 -m 繁中代理.cli users create alice --password <密碼> --workdirs /path/to/repo
  python3 -m 繁中代理.cli auth login alice
  python3 -m 繁中代理.cli --session demo "請讀取 README"

使用者與隔離：
  --user-id 是 dev/test fallback；正式使用建議先 users create，再 auth login。
  已登入時，agent 會用目前登入者的 UserContext 限制 session、tools、skills、memory 與 workdir。
  TESTAGENT2_REQUIRE_LOGIN=1 可要求必須登入後才能執行 agent。

子命令：
  users     管理本機使用者、可用 tools/skills/workdirs
  auth      login / whoami / logout，管理本機 token
  sessions  列出、搜尋、重新命名、匯出 session
"""
    解析器 = argparse.ArgumentParser(description=說明, formatter_class=argparse.RawDescriptionHelpFormatter)
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


def 解析目前使用者上下文(參數: argparse.Namespace) -> 使用者上下文:
    """依 CLI 參數與本機 auth token 解析目前使用者。

    參數：
        參數: argparse namespace，需含 db、workdir、user_id。

    返回值：
        使用者上下文。若要求登入但無 token，會中止程式。
    """
    使用者庫物件 = 使用者庫(參數.db)
    auth資料 = 讀取Auth檔案()
    要求登入 = os.getenv("TESTAGENT2_REQUIRE_LOGIN") == "1"
    if 要求登入:
        if not auth資料 or not auth資料.get("token"):
            raise SystemExit("尚未登入。請先執行：testagent2 auth login <username>")
        try:
            上下文 = 使用者庫物件.驗證登入Token(str(auth資料["token"]))
        except ValueError:
            raise SystemExit("登入 token 無效。請重新執行：testagent2 auth login <username>") from None
        if 參數.user_id and 參數.user_id != 上下文.user_id:
            raise SystemExit("--user-id 與目前登入者不一致，請改用目前登入者或重新登入。")
        return 上下文
    if 參數.user_id:
        try:
            return 使用者庫物件.建立使用者上下文(user_id=參數.user_id, 工作目錄=參數.workdir)
        except ValueError:
            工作目錄 = Path(參數.workdir).expanduser().resolve()
            return 使用者上下文(
                user_id=參數.user_id,
                username=參數.user_id,
                display_name=參數.user_id,
                roles=["user"],
                enabled_tools=set(),
                enabled_skills=set(),
                skill_roots=[],
                allowed_workdirs=[工作目錄],
                memory_home=取得預設記憶根目錄(參數.user_id),
                is_admin=False,
            )
    if auth資料 and auth資料.get("token"):
        try:
            return 使用者庫物件.驗證登入Token(str(auth資料["token"]))
        except ValueError:
            raise SystemExit("登入 token 無效。請重新執行：testagent2 auth login <username>") from None
    return 建立預設使用者上下文(參數.workdir)


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
    使用者上下文物件 = 解析目前使用者上下文(參數)
    return 代理執行階段(
        工作階段庫物件=工作階段庫物件,
        模型供應商物件=模型供應商物件,
        模型名稱=參數.model,
        供應商名稱="fake" if 參數.mode == "fake" else "gemini-adc",
        工作目錄=參數.workdir,
        最大迭代次數=參數.max_iters,
        模型模式=參數.mode,
        user_id=使用者上下文物件.user_id,
        使用者上下文物件=使用者上下文物件,
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
        try:
            return 工作階段庫物件.解析Resume工作階段(參照, user_id=user_id, source=source)
        except PermissionError:
            return None
    sessions = 工作階段庫物件.列出工作階段(limit=200, include_archived=include_archived, source=source, user_id=user_id)
    for session in sessions:
        if str(session.get("title") or "") == 參照:
            return 工作階段庫物件.解析Resume工作階段(str(session["id"]), user_id=user_id, source=source)
    lowered = 參照.lower()
    for session in sessions:
        if lowered in str(session.get("title") or "").lower() or lowered in str(session.get("id") or "").lower():
            return 工作階段庫物件.解析Resume工作階段(str(session["id"]), user_id=user_id, source=source)
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
    if not getattr(參數, "user_id", None):
        參數.user_id = 解析目前使用者上下文(argparse.Namespace(db=參數.db, workdir=os.getcwd(), user_id=None)).user_id
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
    if 參數.sessions_command == "search":
        結果 = 工作階段庫物件.搜尋工作階段(參數.query, limit=參數.limit, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
        if 參數.json:
            印出JSON({"matches": 結果, "total_count": len(結果)})
        else:
            if not 結果:
                print("沒有符合的 session。")
            for i, match in enumerate(結果, start=1):
                print(f"{i:>2}. {match.get('session_id')}  {match.get('title') or ''}")
                print(f"    {取訊息摘要({'content': match.get('snippet') or ''}, 120)}")
        return
    if 參數.sessions_command == "rename":
        工作階段庫物件.重新命名工作階段(參數.session_id, 參數.title, user_id=參數.user_id)
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
        if 參數.resume or 參數.continue_session:
            參照 = 參數.resume or 參數.continue_session
            解析後 = 解析工作階段參照(self.工作階段庫物件, 參照, include_archived=參數.include_archived, source=參數.source, user_id=參數.user_id)
            if 解析後:
                self.目前工作階段識別碼 = 解析後
            else:
                print(f"找不到可 resume 的 session：{參照 if 參照 != '__latest__' else 'latest'}；將以新 session 開始。")
                self.目前工作階段識別碼 = None
        self.上一個使用者訊息: str | None = None
        self.待選Resume工作階段清單: list[dict[str, Any]] | None = None

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
            if self.待選Resume工作階段清單 and 內容.isdigit():
                self.選擇Resume序號(int(內容))
                continue
            self.待選Resume工作階段清單 = None
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
            self.命令New(參數列)
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
        print("  /new [session_id]     建立並切換到新 session；省略 id 時自動產生")
        print("  /resume [id|title]    resume 指定 session；省略時列出近期 sessions 可用數字選擇")
        print("  /sessions [list|browse|search|rename|export]  管理 session history")
        print("  /history              顯示目前 session 訊息")
        print("  /retry                rewind 並重送上一個 user turn")
        print("  /undo                 soft-delete 最後一個 user turn 起的訊息")
        print("  /model [name]         顯示或切換目前模型")
        print("  /tools                列出工具與 handler 狀態")
        print("  /skills               列出內建 Hermes skills")
        print("  /status               顯示目前 REPL 狀態")

    def 命令New(self, 參數列: list[str]) -> None:
        """建立並切換到新的 session id。

        參數：
            參數列: 可選自訂 session id。

        返回值：None。此命令只建立/切換 session，不呼叫模型。
        """
        新識別碼 = 參數列[0] if 參數列 else 產生新工作階段識別碼()
        self.工作階段庫物件.建立或讀取工作階段(
            新識別碼,
            source=self.參數.source,
            user_id=self.參數.user_id,
            model=self.參數.model,
            model_config=解析模型設定(self.參數, self.解析器),
            cwd=self.參數.workdir,
        )
        self.目前工作階段識別碼 = 新識別碼
        self.上一個使用者訊息 = None
        self.待選Resume工作階段清單 = None
        print(f"已建立並切換到新 session：{新識別碼}")

    def 選擇Resume序號(self, 序號: int) -> None:
        """使用 /resume 無參數列出的候選清單進行數字選擇。"""
        清單 = self.待選Resume工作階段清單 or []
        if 序號 < 1 or 序號 > len(清單):
            print(f"選項超出範圍：{序號}")
            return
        session = 清單[序號 - 1]
        self.目前工作階段識別碼 = self.工作階段庫物件.解析Resume工作階段(str(session["id"]), user_id=self.參數.user_id, source=self.參數.source)
        self.待選Resume工作階段清單 = None
        print(f"已 resume：{self.目前工作階段識別碼}  title={session.get('title') or ''}")

    def 命令Resume(self, 參數列: list[str]) -> None:
        """處理 /resume 命令。

        參數：
            參數列: shlex 解析後的命令參數。

        返回值：None。成功時更新目前 session id。
        """
        if not 參數列:
            sessions = self.工作階段庫物件.列出工作階段(limit=10, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
            if not sessions:
                print("沒有可 resume 的 session。")
                return
            self.待選Resume工作階段清單 = sessions
            print("請輸入數字選擇要 resume 的 session：")
            for i, session in enumerate(sessions, start=1):
                print(f"{i:>2}. {session.get('id')}  {session.get('title') or ''}  updated={格式化時間戳(session.get('updated_at'))}")
            return
        原始 = " ".join(參數列)
        解析後 = 解析工作階段參照(self.工作階段庫物件, 原始, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
        if not 解析後:
            print(f"找不到 session：{原始}")
            return
        self.目前工作階段識別碼 = 解析後
        self.待選Resume工作階段清單 = None
        session = self.工作階段庫物件.讀取工作階段(解析後) or {}
        print(f"已 resume：{self.目前工作階段識別碼}  title={session.get('title') or ''}")

    def 命令Sessions(self, 參數列: list[str]) -> None:
        """處理 /sessions 命令。

        參數：
            參數列: 可選 limit。

        返回值：None。近期 session 表格會輸出到 stdout。
        """
        if not 參數列:
            參數列 = ["browse", "10"]
        if 參數列[0].isdigit():
            參數列 = ["browse", 參數列[0]]
        子命令 = 參數列[0].lower()
        if 子命令 in {"list", "ls"}:
            try:
                limit = int(參數列[1]) if len(參數列) > 1 else 10
            except ValueError:
                limit = 10
            sessions = self.工作階段庫物件.列出工作階段(limit=limit, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
            印出工作階段表格(sessions)
            return
        if 子命令 in {"browse", "b"}:
            try:
                limit = int(參數列[1]) if len(參數列) > 1 else 10
            except ValueError:
                limit = 10
            sessions = self.工作階段庫物件.列出工作階段(limit=limit, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
            印出工作階段表格(sessions, self.工作階段庫物件, 顯示預覽=True)
            return
        if 子命令 == "search":
            查詢 = " ".join(參數列[1:]).strip()
            if not 查詢:
                print("用法：/sessions search <query>")
                return
            matches = self.工作階段庫物件.搜尋工作階段(查詢, limit=10, include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
            if not matches:
                print("沒有符合的 session。")
                return
            for i, match in enumerate(matches, start=1):
                print(f"{i:>2}. {match.get('session_id')}  {match.get('title') or ''}")
                print(f"    {取訊息摘要({'content': match.get('snippet') or ''}, 120)}")
            return
        if 子命令 == "rename":
            if len(參數列) < 3:
                print("用法：/sessions rename <session_id> <title>")
                return
            self.工作階段庫物件.重新命名工作階段(參數列[1], " ".join(參數列[2:]), user_id=self.參數.user_id)
            print(f"已重新命名：{參數列[1]}")
            return
        if 子命令 == "export":
            if len(參數列) < 2:
                print("用法：/sessions export <output.jsonl>")
                return
            結果 = self.工作階段庫物件.匯出工作階段JSONL(參數列[1], include_archived=self.參數.include_archived, source=self.參數.source, user_id=self.參數.user_id)
            印出JSON(結果)
            return
        print("未知 /sessions 子命令。用法：/sessions [list|browse|search|rename|export]")

    def 命令History(self) -> None:
        """處理 /history 命令。

        參數：無。
        返回值：None。印出目前 session active messages。
        """
        if not self.目前工作階段識別碼:
            print("尚未有目前 session。")
            return
        訊息清單 = self.工作階段庫物件.讀取訊息(self.目前工作階段識別碼, user_id=self.參數.user_id)
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
        self.工作階段庫物件.檢查工作階段存取(self.目前工作階段識別碼, user_id=self.參數.user_id)
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
        self.工作階段庫物件.rewind到訊息(self.目前工作階段識別碼, int(row["id"]), user_id=self.參數.user_id)
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
        結果 = self.工作階段庫物件.rewind到訊息(self.目前工作階段識別碼, int(row["id"]), user_id=self.參數.user_id)
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
        工作階段庫物件.封存工作階段(參數.archive_session, user_id=參數.user_id)
        印出JSON({"session_id": 參數.archive_session, "archived": True})
        return True
    if 參數.unarchive_session:
        工作階段庫物件.取消封存工作階段(參數.unarchive_session, user_id=參數.user_id)
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
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        auth解析器 = 建立Auth參數解析器()
        參數 = auth解析器.parse_args(sys.argv[2:])
        執行Auth子命令(參數)
        return
    if len(sys.argv) > 1 and sys.argv[1] == "users":
        users解析器 = 建立Users參數解析器()
        參數 = users解析器.parse_args(sys.argv[2:])
        執行Users子命令(參數)
        return
    解析器 = 建立參數解析器()
    參數 = 解析器.parse_args()
    if not 參數.user_id and (參數.query or 參數.message or not (參數.archive_session or 參數.unarchive_session or 參數.session_search)):
        參數.user_id = 解析目前使用者上下文(參數).user_id
    if 執行一次性操作(參數, 解析器):
        return
    if 參數.query or 參數.message:
        執行單次訊息(參數, 解析器)
        return
    互動CLI(參數, 解析器).執行()


if __name__ == "__main__":
    執行主程式()
