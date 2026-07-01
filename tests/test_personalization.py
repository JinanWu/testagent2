"""測試個人化使用者、session、工具、技能與記憶隔離。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.工作階段上下文 import 設定目前使用者, 讀取目前使用者識別碼
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.模型供應商 import 假模型供應商
from 繁中代理.工具 import 建立預設工具登錄器
from 繁中代理.使用者 import 使用者上下文, 使用者庫, 預設登入Token有效秒數, 雜湊Token

專案根目錄 = Path(__file__).resolve().parents[1]


def 建立上下文(user_id: str, tmp_path: Path, tools: set[str] | None = None, skills: set[str] | None = None, workdir: Path | None = None) -> 使用者上下文:
    """建立測試用使用者上下文。

    參數：
        user_id: 使用者識別碼。
        tmp_path: pytest 暫存目錄。
        tools: 可用工具集合；None 表示全部。
        skills: 可用技能集合；None 表示全部。
        workdir: 允許工作目錄。

    返回值：
        測試用使用者上下文。
    """
    return 使用者上下文(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        roles=["user"],
        enabled_tools=tools,
        enabled_skills=skills,
        skill_roots=[tmp_path / "skills"],
        allowed_workdirs=[workdir or tmp_path],
        memory_home=tmp_path / "memory" / user_id,
        is_admin=False,
    )


def 寫入技能(root: Path, category: str, name: str, description: str) -> None:
    """建立測試用 SKILL.md。

    參數：
        root: skills root。
        category: 分類目錄。
        name: 技能名稱。
        description: 技能描述。

    返回值：None。
    """
    路徑 = root / category / name
    路徑.mkdir(parents=True)
    (路徑 / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n", encoding="utf-8")


def test_runtime只傳user_id會載入完整使用者上下文(tmp_path):
    """確認 user_id-only runtime 不會沿用 local/admin 權限。"""
    db = tmp_path / "users.sqlite3"
    使用者資料庫 = 使用者庫(db)
    使用者資料庫.建立使用者("alice", password="pw", enabled_tools=["read_file"], enabled_skills=["skill_a"], skill_roots=[], allowed_workdirs=[str(tmp_path / "allowed")])
    alice = 使用者資料庫.讀取使用者(username="alice")
    assert alice is not None
    runtime = 代理執行階段(工作階段庫(db), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), user_id=str(alice["id"]))
    assert runtime.使用者上下文物件.username == "alice"
    assert runtime.使用者上下文物件.enabled_tools == {"read_file"}
    assert runtime.使用者上下文物件.enabled_skills == {"skill_a"}
    assert runtime.使用者上下文物件.is_admin is False
    assert runtime.使用者上下文物件.memory_home == (tmp_path.home() / ".testagent2" / "users" / str(alice["id"])).expanduser().resolve()
    工具名稱 = {結構["function"]["name"] for 結構 in runtime.工具登錄器物件.列出工具結構()}
    assert "read_file" in 工具名稱
    assert "terminal" not in 工具名稱


def test_runtime拒絕不一致的user_id與使用者上下文(tmp_path):
    """確認 user_id 與非 local 使用者上下文不一致時 fail closed。"""
    alice = 建立上下文("alice", tmp_path)
    with pytest.raises(ValueError, match="不一致"):
        代理執行階段(工作階段庫(tmp_path / "bad.sqlite3"), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), user_id="bob", 使用者上下文物件=alice)


def test_runtime覆寫local使用者時不修改傳入上下文(tmp_path):
    """確認 local fallback context 不會被 runtime 就地修改。"""
    local = 使用者上下文(allowed_workdirs=[tmp_path])
    runtime = 代理執行階段(工作階段庫(tmp_path / "local.sqlite3"), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), user_id="alice", 使用者上下文物件=local)
    assert runtime.user_id == "alice"
    assert runtime.使用者上下文物件 is not local
    assert local.user_id == "local"
    assert local.username == "local"
def test_rewind會重設目前使用者上下文(tmp_path):
    """確認 runtime rewind 入口會校正目前使用者 ContextVar。"""
    庫 = 工作階段庫(tmp_path / "rewind.sqlite3")
    alice = 建立上下文("alice", tmp_path)
    bob = 建立上下文("bob", tmp_path)
    alice_runtime = 代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=alice)
    bob_runtime = 代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=bob)
    alice_runtime.執行使用者訊息("hello", 工作階段識別碼="alice-rewind")
    bob_runtime.執行使用者訊息("hi", 工作階段識別碼="bob-rewind")
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id=? ORDER BY id LIMIT 1", ("alice-rewind",)).fetchone()["id"]
    assert 讀取目前使用者識別碼() == "bob"
    alice_runtime.rewind到訊息("alice-rewind", target)
    assert 讀取目前使用者識別碼() == "alice"


def test_session_owner_不可被其他使用者_resume或覆蓋(tmp_path):
    """確認 session owner 不會被跨使用者覆蓋。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    alice = 建立上下文("alice", tmp_path)
    bob = 建立上下文("bob", tmp_path)
    代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=alice).執行使用者訊息("你好", 工作階段識別碼="shared")
    with pytest.raises(PermissionError):
        代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=bob).執行使用者訊息("偷看", 工作階段識別碼="shared")
    assert 庫.讀取工作階段("shared")["user_id"] == "alice"


def test_session_read_rename_archive_rewind都檢查_owner(tmp_path):
    """確認 direct read、rename、archive、rewind 都拒絕跨使用者。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("owned", user_id="alice")
    庫.寫入訊息清單(sid, [{"role": "user", "content": "秘密"}, {"role": "assistant", "content": "回答"}])
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()["id"]
    with pytest.raises(PermissionError):
        庫.讀取工作階段全文(sid, user_id="bob")
    with pytest.raises(PermissionError):
        庫.捲動工作階段訊息(sid, target, user_id="bob")
    with pytest.raises(PermissionError):
        庫.重新命名工作階段(sid, "bad", user_id="bob")
    with pytest.raises(PermissionError):
        庫.封存工作階段(sid, user_id="bob")
    with pytest.raises(PermissionError):
        庫.rewind到訊息(sid, target, user_id="bob")


def test_session_search_tool_忽略模型傳入_user_id並使用目前上下文(tmp_path):
    """確認 session_search tool 不能靠參數冒充其他 user。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("alice-session", user_id="alice")
    庫.寫入訊息清單(sid, [{"role": "user", "content": "alice secret"}])
    設定目前使用者("bob", 建立上下文("bob", tmp_path))
    登錄器 = 建立預設工具登錄器(tmp_path, 建立上下文("bob", tmp_path, tools={"session_search"}))
    結果 = json.loads(登錄器.呼叫工具("session_search", {"session_id": sid, "user_id": "alice", "db_path": str(tmp_path / "sessions.sqlite3")}))
    assert 結果["success"] is False
    assert "無權" in 結果["error"]


def test_tool_schema與硬呼叫都依使用者權限(tmp_path):
    """確認不允許的 tool 不暴露，硬呼叫也被拒。"""
    上下文 = 建立上下文("alice", tmp_path, tools={"read_file"})
    登錄器 = 建立預設工具登錄器(tmp_path, 上下文)
    工具名稱 = {結構["function"]["name"] for 結構 in 登錄器.列出工具結構()}
    assert "read_file" in 工具名稱
    assert "terminal" not in 工具名稱
    結果 = json.loads(登錄器.呼叫工具("terminal", {"command": "pwd"}))
    assert 結果["permission_denied"] is True


def test_file與terminal工具限制_workdir(tmp_path):
    """確認檔案與 terminal 工具不能越出 allowed_workdirs。"""
    允許 = tmp_path / "allowed"
    禁止 = tmp_path / "denied"
    允許.mkdir()
    禁止.mkdir()
    (禁止 / "secret.txt").write_text("secret", encoding="utf-8")
    上下文 = 建立上下文("alice", tmp_path, tools={"read_file", "terminal"}, workdir=允許)
    登錄器 = 建立預設工具登錄器(允許, 上下文)
    讀取結果 = json.loads(登錄器.呼叫工具("read_file", {"path": str(禁止 / "secret.txt")}))
    assert 讀取結果["success"] is False and "超出" in 讀取結果["error"]
    終端結果 = json.loads(登錄器.呼叫工具("terminal", {"command": "pwd", "workdir": str(禁止)}))
    assert 終端結果["success"] is False and "超出" in 終端結果["error"]


def test_skill_prompt與skill_view依使用者隔離(tmp_path):
    """確認 prompt skill 摘要與 skill_view 都依使用者技能權限隔離。"""
    寫入技能(tmp_path / "skills", "cat", "skill_a", "A only")
    寫入技能(tmp_path / "skills", "cat", "skill_b", "B only")
    上下文 = 建立上下文("alice", tmp_path, tools={"skills_list", "skill_view"}, skills={"skill_a"})
    runtime = 代理執行階段(工作階段庫(tmp_path / "s.sqlite3"), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=上下文)
    prompt = runtime.建立系統提示詞("skill-session")
    assert "skill_a" in prompt
    assert "skill_b" not in prompt
    可讀 = json.loads(runtime.工具登錄器物件.呼叫工具("skill_view", {"name": "skill_a"}))
    不可讀 = json.loads(runtime.工具登錄器物件.呼叫工具("skill_view", {"name": "skill_b"}))
    assert 可讀["success"] is True
    assert 不可讀["success"] is False and "無權" in 不可讀["error"]


def test_memory依使用者隔離並注入各自_prompt(tmp_path):
    """確認 memory tool 寫入與 prompt 注入都使用 user-scoped memory_home。"""
    alice = 建立上下文("alice", tmp_path, tools={"memory"})
    bob = 建立上下文("bob", tmp_path, tools={"memory"})
    alice_runtime = 代理執行階段(工作階段庫(tmp_path / "a.sqlite3"), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=alice)
    bob_runtime = 代理執行階段(工作階段庫(tmp_path / "b.sqlite3"), 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=bob)
    寫入結果 = json.loads(alice_runtime.工具登錄器物件.呼叫工具("memory", {"action": "add", "target": "user", "content": "Alice 偏好"}))
    assert 寫入結果["success"] is True
    assert "Alice 偏好" in alice_runtime.建立系統提示詞("alice")
    assert "Alice 偏好" not in bob_runtime.建立系統提示詞("bob")


def test_cli_help揭露使用者與登入流程():
    """確認 help 會揭露 users/auth 子命令與常用登入流程。"""
    主說明 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--help"], cwd=專案根目錄, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert 主說明.returncode == 0, 主說明.stdout
    assert "users --help" in 主說明.stdout
    assert "auth --help" in 主說明.stdout
    assert "TESTAGENT2_REQUIRE_LOGIN" in 主說明.stdout

    使用者說明 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "users", "--help"], cwd=專案根目錄, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert 使用者說明.returncode == 0, 使用者說明.stdout
    assert "users create alice" in 使用者說明.stdout
    assert "set-tools" in 使用者說明.stdout
    assert "set-skills" in 使用者說明.stdout

    登入說明 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "auth", "--help"], cwd=專案根目錄, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert 登入說明.returncode == 0, 登入說明.stdout
    assert "auth login alice" in 登入說明.stdout
    assert "whoami" in 登入說明.stdout
    assert "logout" in 登入說明.stdout


def test_cli_auth_login_whoami與執行使用登入者(tmp_path):
    """確認 CLI 可建立使用者、登入、whoami，且 agent 執行使用登入者。"""
    db = tmp_path / "auth.sqlite3"
    auth_file = tmp_path / "auth.json"
    env = os.environ | {"TESTAGENT2_AUTH_FILE": str(auth_file), "AIAGENT_MODEL_MODE": "fake"}
    建立 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "users", "--db", str(db), "create", "alice", "--password", "pw", "--workdirs", str(tmp_path)], cwd=專案根目錄, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert 建立.returncode == 0, 建立.stdout
    登入 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "auth", "--db", str(db), "login", "alice", "--password", "pw"], cwd=專案根目錄, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert 登入.returncode == 0, 登入.stdout
    whoami = subprocess.run([sys.executable, "-m", "繁中代理.cli", "auth", "--db", str(db), "whoami"], cwd=專案根目錄, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert "alice" in whoami.stdout
    執行 = subprocess.run([sys.executable, "-m", "繁中代理.cli", "--db", str(db), "--mode", "fake", "--session", "cli-user", "hello"], cwd=專案根目錄, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    assert 執行.returncode == 0, 執行.stdout
    工作階段 = 工作階段庫(db).讀取工作階段("cli-user")
    assert 工作階段 and 工作階段["user_id"] != "local"


def test_登入Token預設一天後過期(tmp_path):
    """確認登入 token 預設在 24 小時後過期。"""
    db = tmp_path / "auth.sqlite3"
    庫 = 使用者庫(db)
    庫.建立使用者("alice", password="pw")
    使用者 = 庫.讀取使用者(username="alice")
    開始 = time.time()
    token = 庫.建立登入Token(str(使用者["id"]))
    結束 = time.time()
    資料列 = 庫.連線.execute(
        "SELECT expires_at FROM auth_sessions WHERE token_hash=?",
        (雜湊Token(token),),
    ).fetchone()
    assert 資料列 and 資料列["expires_at"] is not None
    過期時間 = float(資料列["expires_at"])
    assert 開始 + 預設登入Token有效秒數 <= 過期時間 <= 結束 + 預設登入Token有效秒數
    庫.驗證登入Token(token)
    庫.連線.execute(
        "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
        (time.time() - 1, 雜湊Token(token)),
    )
    with pytest.raises(ValueError, match="已過期"):
        庫.驗證登入Token(token)
