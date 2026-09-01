"""PUB P07 真實使用者權限 mutation 與 endpoint status lifecycle。"""

import json
import sqlite3
import threading


import pytest

from 繁中代理.使用者 import 使用者庫, 權限更新錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.權限協調 import (
    SQLite發布權限協調器,
    發布權限協調錯誤,
    鎖定確認端點可執行,
)


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "p07.db"
    初始化發布介面資料庫(路徑)
    庫 = 使用者庫(路徑, SQLite發布權限協調器())
    owner = 庫.建立使用者("owner", enabled_tools=["*"], enabled_skills=["*"], skill_roots=["*"])["id"]
    other = 庫.建立使用者("other")["id"]
    return 路徑, 庫, owner, other


def _加端點(連線, endpoint, owner, *, tools=(), skills=(), status="active"):
    account = f"account-{endpoint}"
    version = f"version-{endpoint}"
    manifest = {"permission_revision": "perm-r1", "skills": [
        {"name": name, "content_sha256_reference": "a" * 64} for name in skills
    ]}
    連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (account, 1.0))
    連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)",
        (endpoint, owner, account, endpoint, status, 1.0, 1.0),
    )
    連線.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (version, endpoint, 1, "req", "prompt", json.dumps(list(skills)), json.dumps(list(tools)),
         "{}", "runtime-r1", "{}", "{}", json.dumps(manifest), None, "{}", 0, owner, 1.0),
    )
    連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (version, endpoint))
    return version


def _狀態(庫):
    return dict(庫.連線.execute("SELECT id,status FROM published_endpoints ORDER BY id"))


def test_工具撤銷只停用owner中current_pin不再獲准者(tmp_path):
    _, 庫, owner, other = _建立資料庫(tmp_path)
    _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    _加端點(庫.連線, "endpoint-b", owner, tools=("tool.b",))
    _加端點(庫.連線, "endpoint-other", other, tools=("tool.a",))

    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert _狀態(庫) == {
        "endpoint-a": "disabled", "endpoint-b": "active", "endpoint-other": "active",
    }
    assert 庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0] == '["tool.b"]'


def test_技能撤銷與root_narrowing依實際manifest停用且workdir不影響(tmp_path):
    _, 庫, owner, _ = _建立資料庫(tmp_path)
    _加端點(庫.連線, "with-skill", owner, skills=("skill.a",))
    _加端點(庫.連線, "without-skill", owner)

    庫.設定權限欄位("owner", "allowed_workdirs_json", ["/tmp"])
    assert set(_狀態(庫).values()) == {"active"}
    庫.設定權限欄位("owner", "skill_roots_json", ["root-a"])
    assert _狀態(庫) == {"with-skill": "disabled", "without-skill": "active"}


def test_星號與空清單皆不限制且擴張或noop不停用(tmp_path):
    _, 庫, owner, _ = _建立資料庫(tmp_path)
    _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.a"])
    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.a", "tool.b"])
    庫.設定權限欄位("owner", "enabled_tools_json", [])
    assert _狀態(庫)["endpoint-a"] == "active"
    assert 庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0] == "[]"


def test_畸形current_snapshot使設定與所有端點狀態一起rollback(tmp_path):
    _, 庫, owner, _ = _建立資料庫(tmp_path)
    _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    _加端點(庫.連線, "endpoint-b", owner, tools=("tool.b",))
    庫.連線.execute("DROP TRIGGER published_endpoint_versions_no_update")
    庫.連線.execute(
        "UPDATE published_endpoint_versions SET allowed_tools_json='{}' WHERE id='version-endpoint-b'"
    )

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$") as 錯誤:
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert set(_狀態(庫).values()) == {"active"}
    assert json.loads(庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0]) == ["*"]


def test_協調callback目標於建構時固定且失敗rollback(tmp_path):
    路徑 = tmp_path / "callback.db"
    初始化發布介面資料庫(路徑)
    class 協調器:
        def 協調權限變更(self, 連線, 擁有者識別碼, 欄位, 舊項目, 新項目, 更新時間) -> None:
            raise RuntimeError("private-tool-name")
    協調 = 協調器()
    庫 = 使用者庫(路徑, 協調)
    owner = 庫.建立使用者("owner")["id"]
    _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    協調器.協調權限變更 = lambda self, *_: None  # type: ignore[method-assign]

    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    assert _狀態(庫)["endpoint-a"] == "active"
    assert json.loads(庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0]) == ["*"]


def test_重新確認須全部獲准且封存terminal(tmp_path):
    _, 庫, owner, _ = _建立資料庫(tmp_path)
    _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    協調器 = SQLite發布權限協調器()
    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    with pytest.raises(發布權限協調錯誤, match="^端點狀態變更遭拒$"):
        協調器.重新確認端點(庫.連線, owner, "endpoint-a", 3.0)
    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.a"])
    協調器.重新確認端點(庫.連線, owner, "endpoint-a", 4.0)
    assert _狀態(庫)["endpoint-a"] == "active"
    協調器.封存端點(庫.連線, owner, "endpoint-a", 5.0)
    assert _狀態(庫)["endpoint-a"] == "archived"
    with pytest.raises(發布權限協調錯誤):
        協調器.重新確認端點(庫.連線, owner, "endpoint-a", 6.0)


def test_呼叫交易鎖定後依exact_status與version判斷(tmp_path):
    路徑, 庫, owner, _ = _建立資料庫(tmp_path)
    version = _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    呼叫連線 = sqlite3.connect(路徑, isolation_level=None)
    呼叫連線.execute("BEGIN IMMEDIATE")
    鎖定確認端點可執行(呼叫連線, "endpoint-a", version)
    呼叫連線.execute("COMMIT")
    庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
    呼叫連線.execute("BEGIN IMMEDIATE")
    with pytest.raises(發布權限協調錯誤, match="^端點目前不可執行$"):
        鎖定確認端點可執行(呼叫連線, "endpoint-a", version)
    呼叫連線.execute("ROLLBACK")


@pytest.mark.parametrize("控制類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_協調控制流保留exact_type且rollback設定(控制類型, tmp_path):
    路徑 = tmp_path / f"control-{控制類型.__name__}.db"
    初始化發布介面資料庫(路徑)
    class 協調器:
        def 協調權限變更(self, 連線, 擁有者識別碼, 欄位, 舊項目, 新項目, 更新時間) -> None:
            raise 控制類型("control-marker")
    庫 = 使用者庫(路徑, 協調器())
    owner = 庫.建立使用者("owner")["id"]

    with pytest.raises(控制類型, match="^control-marker$"):
        庫.設定權限欄位("owner", "enabled_tools_json", ["tool.a"])

    assert json.loads(庫.連線.execute(
        "SELECT enabled_tools_json FROM user_settings WHERE user_id=?", (owner,)
    ).fetchone()[0]) == ["*"]


def test_呼叫先鎖定時撤銷等待且只允許inflight_pin完成(tmp_path):
    路徑, 庫, owner, _ = _建立資料庫(tmp_path)
    version = _加端點(庫.連線, "endpoint-a", owner, tools=("tool.a",))
    呼叫連線 = sqlite3.connect(路徑, isolation_level=None)
    呼叫連線.execute("BEGIN IMMEDIATE")
    鎖定確認端點可執行(呼叫連線, "endpoint-a", version)
    已嘗試 = threading.Event()
    完成 = threading.Event()

    def 撤銷():
        另一庫 = 使用者庫(路徑, SQLite發布權限協調器())
        另一庫.連線.set_trace_callback(
            lambda sql: 已嘗試.set() if sql == "BEGIN IMMEDIATE" else None
        )
        另一庫.設定權限欄位("owner", "enabled_tools_json", ["tool.b"])
        完成.set()

    執行緒 = threading.Thread(target=撤銷)
    執行緒.start()
    assert 已嘗試.wait(2) and not 完成.is_set()
    呼叫連線.execute("COMMIT")
    執行緒.join(2)
    assert 完成.is_set() and _狀態(庫)["endpoint-a"] == "disabled"
