"""CP4 production 發布管理協調器與真實 primitive 端對端測試。"""
from __future__ import annotations

import sqlite3
import threading
import json
import traceback
from pathlib import Path

import pytest

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.憑證.加密 import AESGCM密文, AESGCM憑證封套
from 繁中代理.發布介面.技能套件.協調器 import 技能套件協調器
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.規劃.擁有者能力 import 擁有者能力轉接器
from 繁中代理.發布介面.規劃.發布管理 import 發布管理協調器
from 繁中代理.發布介面.規劃.端點發布 import SQLite端點發布服務
from 繁中代理.發布介面.規劃.權限協調 import SQLite發布權限協調器, 權限協調器
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.路由.規劃發布 import (
    發布確認, 端點發布結果, 管理操作錯誤, 建立發布版本路由器,
)


def _綱要() -> dict:
    """建立真實 Planner 已驗證後會保存的 authority outline。"""
    return {
        "endpoint_name": "Alpha", "suggested_slug": "alpha-api",
        "behavior_summary": "精確回答", "selected_skills": ["alpha"],
        "recommended_tools": ["alpha-tool"], "tool_capabilities": {"alpha-tool": "查詢"},
        "system_prompt": "只根據技能回答", "input_schema": None,
        "response_schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                            "required": ["answer"], "additionalProperties": False},
        "human_docs": "呼叫後回傳答案。",
        "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
        "warnings": [],
    }


def _確認(草稿識別碼: str, *, 短名: str = "alpha-api") -> 發布確認:
    """建立只確認顯示值、不提供 persistence snapshot 的 route DTO。"""
    綱要 = _綱要()
    return 發布確認(草稿識別碼, 短名, {
        "system_prompt": 綱要["system_prompt"], "response_schema": 綱要["response_schema"],
        "rate_limit": 綱要["rate_limit"],
    })


def _建立環境(tmp_path: Path):
    """以真實 Web SQLite owner、tool release、遷移 DB、AES 與 bundle dirs 組裝服務。"""
    技能根 = tmp_path / "skills"
    技能 = 技能根 / "alpha"
    技能.mkdir(parents=True)
    (技能 / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n", encoding="utf-8",
    )
    使用者庫物件 = 使用者庫(tmp_path / "web.sqlite3")
    使用者 = 使用者庫物件.建立使用者(
        "owner", roles=["admin"], enabled_tools=["alpha-tool"], enabled_skills=["alpha"],
        skill_roots=[str(技能根)],
    )
    擁有者 = str(使用者["id"])
    工具庫 = 工具發布庫()
    工具 = 工具定義(
        "alpha-tool", "Alpha tool", {"type": "object", "properties": {}}, lambda _: "ok",
    )
    工具庫.登錄發布(工具發布描述("release-1", (工具發布註冊("revision-1", 工具),)))
    解析器 = 擁有者能力轉接器(使用者庫物件, 工具庫, "release-1")
    草稿服務 = 規劃服務(存續秒數=1000, 識別碼產生器=lambda: "draft-1")
    草稿服務.建立授權草稿(
        權限協調器(解析器), 擁有者, "建立 Alpha API", _綱要(),
        ("alpha",), ("alpha-tool",), 現在=10.0,
    )
    資料庫 = tmp_path / "published.sqlite3"
    初始化發布介面資料庫(資料庫)
    套件根 = tmp_path / "bundles"
    封套 = AESGCM憑證封套({1: b"K" * 32}, 1, 隨機位元組=lambda 長度: b"N" * 長度)
    次數: dict[str, int] = {}
    鎖 = threading.Lock()

    def 識別碼(前綴: str) -> str:
        """為並行測試提供每種 graph identity 的原子序號。"""
        with 鎖:
            次數[前綴] = 次數.get(前綴, 0) + 1
            return f"{前綴}-{次數[前綴]}"

    def 未使用識別() -> str:
        """prepared P04 路徑不得呼叫 legacy 識別工廠。"""
        return "unused"

    端點服務 = SQLite端點發布服務(
        資料庫, 未使用識別, 未使用識別, 未使用識別, 未使用識別, lambda: 20.0,
    )
    協調器 = 技能套件協調器(套件根, 孤兒保留秒數=3600, 時鐘=lambda: 20.0)
    服務 = 發布管理協調器(
        草稿服務=草稿服務, 擁有者解析器=解析器,
        套件發布器物件=技能套件發布器(套件根), 套件協調器物件=協調器,
        端點發布服務=端點服務, 憑證封套=封套, 時鐘=lambda: 20.0,
        識別碼產生器=識別碼, 隨機位元組=lambda 長度: bytes(range(長度)),
    )
    return {
        "服務": 服務, "資料庫": 資料庫, "套件根": 套件根, "封套": 封套,
        "使用者庫": 使用者庫物件, "擁有者": 擁有者, "解析器": 解析器,
        "草稿服務": 草稿服務, "端點服務": 端點服務, "協調器": 協調器,
    }


def test_成功建立單一圖形收據稽核且明文只回一次(tmp_path, caplog):
    環境 = _建立環境(tmp_path)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert type(結果) is 端點發布結果
    assert (結果.端點識別碼, 結果.版本識別碼, 結果.版本編號, 結果.狀態) == (
        "endpoint-1", "version-1", 1, "active",
    )
    連線 = sqlite3.connect(環境["資料庫"])
    for 表 in (
        "published_endpoints", "published_endpoint_versions", "endpoint_credentials",
        "service_accounts", "published_skill_bundles", "audit_events",
    ):
        assert 連線.execute(f"SELECT count(*) FROM {表}").fetchone() == (1,)
    憑證 = 連線.execute(
        "SELECT id,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4 FROM endpoint_credentials"
    ).fetchone()
    assert 環境["封套"].解密(AESGCM密文(憑證[1], 憑證[2], 憑證[3]), "endpoint-1", 憑證[0]) == 結果.初始API金鑰
    assert 結果.初始API金鑰.encode() not in 環境["資料庫"].read_bytes()
    assert 結果.初始API金鑰 not in repr(結果)
    assert all(結果.初始API金鑰 not in 紀錄.getMessage() for 紀錄 in caplog.records)
    assert 結果.初始API金鑰.encode() not in (環境["套件根"] / "bundle-1" / "manifest.json").read_bytes()
    稽核 = 連線.execute("SELECT action,resource_id,metadata_json FROM audit_events").fetchone()
    assert 稽核[0:2] == ("endpoint_published", "endpoint-1") and 結果.初始API金鑰 not in 稽核[2]
    assert len(建立發布版本路由器(環境["服務"], lambda: None).routes) == 2


def test_P04權威確認callback在BEGIN_IMMEDIATE內執行(tmp_path, monkeypatch):
    """真實連線追蹤 BEGIN，確認第二次 resolver callback 位於立即交易內。"""
    環境 = _建立環境(tmp_path)
    原始解析 = 環境["解析器"].解析發布能力
    解析時交易狀態: list[bool] = []

    class 追蹤連線(sqlite3.Connection):
        已開始 = False

        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                type(self).已開始 = True
            return super().execute(sql, parameters)

    原始工廠 = 環境["端點服務"]._連線工廠

    def 連線工廠(*參數, **選項):
        return 原始工廠(*參數, factory=追蹤連線, **選項)

    def 追蹤解析(擁有者, 摘要):
        解析時交易狀態.append(追蹤連線.已開始)
        return 原始解析(擁有者, 摘要)

    monkeypatch.setattr(環境["端點服務"], "_連線工廠", 連線工廠)
    monkeypatch.setattr(環境["解析器"], "解析發布能力", 追蹤解析)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert type(結果) is 端點發布結果 and 解析時交易狀態 == [False, True]


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_P04權威確認控制流程保留identity_args且清理實際traceback(tmp_path, monkeypatch, 控制型別):
    """callback 控制流程不得被映射或把金鑰 marker 留在 production traceback。"""
    環境 = _建立環境(tmp_path)
    原始解析 = 環境["解析器"].解析發布能力
    呼叫次數 = 0
    主要 = 控制型別("P04_CALLBACK", "opaque")

    def callback解析(擁有者, 摘要):
        nonlocal 呼叫次數
        呼叫次數 += 1
        if 呼叫次數 == 2:
            raise 主要
        return 原始解析(擁有者, 摘要)

    monkeypatch.setattr(環境["解析器"], "解析發布能力", callback解析)
    with pytest.raises(控制型別) as 捕捉:
        環境["服務"].原子發布(
            擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
        )
    marker = "pk_" + __import__("base64").urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
    assert 捕捉.value is 主要
    assert 捕捉.value.args == ("P04_CALLBACK", "opaque")
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    當前 = 捕捉.value.__traceback__
    while 當前 is not None:
        if "/繁中代理/" in 當前.tb_frame.f_code.co_filename:
            assert marker not in repr(當前.tb_frame.f_locals)
        當前 = 當前.tb_next


def test_DB末段失敗完整rollback並把真實bundle標記孤兒(tmp_path):
    環境 = _建立環境(tmp_path)
    連線 = sqlite3.connect(環境["資料庫"])
    連線.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("audit-1", "audit-1", 1.0, "seed", "success", "system", None,
         "seed", "seed", None, None, None, "{}", 1.0),
    )
    連線.commit()
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 結果 == 管理操作錯誤("internal")
    for 表 in ("published_endpoints", "published_endpoint_versions", "endpoint_credentials",
              "service_accounts", "published_skill_bundles"):
        assert 連線.execute(f"SELECT count(*) FROM {表}").fetchone() == (0,)
    assert not (環境["套件根"] / "bundle-1").exists()
    assert (環境["套件根"] / ".orphaned" / "bundle-1" / "manifest.json").is_file()


def test_成功發布後權限撤銷停用再以權威快照恢復啟用(tmp_path):
    """新版六鍵 manifest 必須完整通過 disable/re-enable 真實 SQLite 生命週期。"""
    環境 = _建立環境(tmp_path)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    連線 = sqlite3.connect(環境["資料庫"], isolation_level=None)
    連線.executescript(
        "CREATE TABLE user_settings (user_id TEXT PRIMARY KEY, enabled_tools_json TEXT, "
        "enabled_skills_json TEXT, skill_roots_json TEXT, allowed_workdirs_json TEXT, "
        "memory_home TEXT, settings_json TEXT, updated_at REAL NOT NULL)"
    )
    連線.execute(
        "INSERT INTO user_settings(user_id,enabled_tools_json,enabled_skills_json,skill_roots_json,"
        "allowed_workdirs_json,memory_home,settings_json,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (環境["擁有者"], '["alpha-tool"]', '["alpha"]', '["*"]', '[]', None, '{}', 20.0),
    )
    協調 = SQLite發布權限協調器()
    try:
        連線.execute("BEGIN IMMEDIATE")
        連線.execute(
            "UPDATE user_settings SET enabled_skills_json=? WHERE user_id=?",
            (json.dumps(["other"], separators=(",", ":")), 環境["擁有者"]),
        )
        協調.協調權限變更(
            連線, 環境["擁有者"], "enabled_skills_json", ("alpha",), ("other",), 21.0,
        )
        連線.execute("COMMIT")
        assert 連線.execute(
            "SELECT status FROM published_endpoints WHERE id=?", (結果.端點識別碼,)
        ).fetchone() == ("disabled",)

        連線.execute(
            "UPDATE user_settings SET enabled_skills_json=? WHERE user_id=?",
            (json.dumps(["alpha"], separators=(",", ":")), 環境["擁有者"]),
        )
        協調.重新確認端點(連線, 環境["擁有者"], 結果.端點識別碼, 22.0)
        assert 連線.execute(
            "SELECT status FROM published_endpoints WHERE id=?", (結果.端點識別碼,)
        ).fetchone() == ("active",)
    finally:
        連線.close()


def test_重複發布只有一個圖形且碰撞成果成為孤兒(tmp_path):
    環境 = _建立環境(tmp_path)
    第一筆 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    第二筆 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert type(第一筆) is 端點發布結果 and 第二筆 == 管理操作錯誤("internal")
    連線 = sqlite3.connect(環境["資料庫"])
    assert 連線.execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)
    assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)
    assert (環境["套件根"] / "bundle-1").is_dir()
    assert (環境["套件根"] / ".orphaned" / "bundle-2").is_dir()


def test_並行發布至多提交一個圖形且不覆寫贏家(tmp_path):
    環境 = _建立環境(tmp_path)
    起跑 = threading.Barrier(3)
    結果列 = []

    def 發布工作():
        """同步開始同一草稿與短名的競爭發布。"""
        起跑.wait()
        結果列.append(環境["服務"].原子發布(
            擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
        ))

    執行緒們 = [threading.Thread(target=發布工作) for _ in range(2)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    起跑.wait()
    for 執行緒 in 執行緒們:
        執行緒.join(10)
    assert all(not 執行緒.is_alive() for 執行緒 in 執行緒們)
    assert sum(type(項目) is 端點發布結果 for 項目 in 結果列) == 1
    assert sum(type(項目) is 管理操作錯誤 for 項目 in 結果列) == 1
    連線 = sqlite3.connect(環境["資料庫"])
    assert 連線.execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)
    assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)


def test_commit前fresh_owner撤銷拒絕並隔離bundle(tmp_path, monkeypatch):
    環境 = _建立環境(tmp_path)
    原解析 = 環境["解析器"].解析發布能力
    呼叫 = 0

    def 漂移解析(擁有者, 摘要):
        """第二次 publish resolver lookup 前撤銷真實 Web owner。"""
        nonlocal 呼叫
        呼叫 += 1
        if 呼叫 == 2:
            環境["使用者庫"].設定使用者停用("owner", True)
        return 原解析(擁有者, 摘要)

    monkeypatch.setattr(環境["解析器"], "解析發布能力", 漂移解析)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 呼叫 == 2 and 結果 == 管理操作錯誤("internal")
    assert sqlite3.connect(環境["資料庫"]).execute(
        "SELECT count(*) FROM published_endpoints"
    ).fetchone() == (0,)
    assert (環境["套件根"] / ".orphaned" / "bundle-1").is_dir()


def test_主要控制流程不被孤兒cleanup控制覆蓋(tmp_path, monkeypatch):
    環境 = _建立環境(tmp_path)
    主要 = SystemExit("主要")

    def 交易控制(*參數, **選項):
        """在 bundle 已耐久後模擬資料庫主要控制流程。"""
        del 參數, 選項
        raise 主要

    def 清理控制(_收據):
        """模擬孤兒隔離期間另一個控制流程。"""
        raise KeyboardInterrupt("清理")

    monkeypatch.setattr(環境["端點服務"], "發布已準備圖形", 交易控制)
    monkeypatch.setattr(環境["協調器"], "標記孤兒", 清理控制)
    with pytest.raises(SystemExit) as 捕捉:
        環境["服務"].原子發布(
            擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
        )
    assert 捕捉.value is 主要 and 捕捉.value.args == ("主要",)


def test_任何前置失敗都不產生bundle資料庫或金鑰(tmp_path):
    環境 = _建立環境(tmp_path)
    熵呼叫 = 0

    def 不得產生(_長度):
        """若前置失敗越過 confirmation boundary 即使測試失敗。"""
        nonlocal 熵呼叫
        熵呼叫 += 1
        raise AssertionError

    環境["服務"]._隨機位元組 = 不得產生
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"],
        確認=發布確認("draft-1", "alpha-api", {"system_prompt": "client-forged"}),
    )
    assert 結果 == 管理操作錯誤("invalid") and 熵呼叫 == 0
    assert not 環境["套件根"].exists()
    assert sqlite3.connect(環境["資料庫"]).execute(
        "SELECT count(*) FROM published_endpoints"
    ).fetchone() == (0,)


@pytest.mark.parametrize("清理錯誤", [RuntimeError("cleanup"), KeyboardInterrupt("cleanup")])
def test_真實publisher耐久性未知保留主要失敗且cleanup失敗留下可協調active成果(tmp_path, monkeypatch, 清理錯誤):
    """parent fsync 不確定後必須使用收據隔離，隔離失敗不得改變主要結果。"""
    環境 = _建立環境(tmp_path)

    def 父同步失敗(名稱):
        """只在原子改名完成後注入 ordinary durability failure。"""
        if 名稱 == "parent_fsync":
            raise OSError("unknown")

    環境["服務"]._套件發布器 = 技能套件發布器(環境["套件根"], 失敗點=父同步失敗)

    def 清理(_收據):
        """模擬 ordinary 或控制流程隔離失敗。"""
        raise 清理錯誤

    monkeypatch.setattr(環境["協調器"], "標記孤兒", 清理)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 結果 == 管理操作錯誤("internal")
    assert (環境["套件根"] / "bundle-1" / "manifest.json").is_file()
    assert sqlite3.connect(環境["資料庫"]).execute(
        "SELECT count(*) FROM published_endpoints"
    ).fetchone() == (0,)


def test_來源技能在publish掃描時swap後restore仍因manifest釘選不符而孤兒(tmp_path, monkeypatch):
    """manifest source_hash 必須同時等於草稿 pin 與 resolver selected skill。"""
    環境 = _建立環境(tmp_path)
    主檔 = tmp_path / "skills" / "alpha" / "SKILL.md"
    原文 = 主檔.read_text(encoding="utf-8")
    原發布 = 環境["服務"]._套件發布器.發布

    def 交換後發布(**選項):
        """只在 publisher 掃描窗口換入 forged skill，完成後恢復來源。"""
        主檔.write_text("---\nname: alpha\ndescription: forged\n---\n# forged\n", encoding="utf-8")
        try:
            return 原發布(**選項)
        finally:
            主檔.write_text(原文, encoding="utf-8")

    monkeypatch.setattr(環境["服務"]._套件發布器, "發布", 交換後發布)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 結果 == 管理操作錯誤("internal")
    assert not (環境["套件根"] / "bundle-1").exists()
    assert (環境["套件根"] / ".orphaned" / "bundle-1" / "manifest.json").is_file()


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程traceback框架不得取得一次性明文marker(tmp_path, monkeypatch, 控制型別):
    """三種控制流程離開協調器時，所有 traceback locals 都不得保有明文字串別名。"""
    環境 = _建立環境(tmp_path)
    主要 = 控制型別("primary")

    def 交易控制(*參數, **選項):
        """在金鑰已建立且 bundle 已耐久後拋指定控制流程。"""
        del 參數, 選項
        raise 主要

    monkeypatch.setattr(環境["端點服務"], "發布已準備圖形", 交易控制)
    with pytest.raises(控制型別) as 捕捉:
        環境["服務"].原子發布(
            擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
        )
    marker = "pk_" + __import__("base64").urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
    assert 捕捉.value is 主要
    目前 = 捕捉.value.__traceback__
    assert 目前 is not None
    while 目前 is not None:
        if "/繁中代理/" in 目前.tb_frame.f_code.co_filename:
            assert marker not in repr(目前.tb_frame.f_locals)
        目前 = 目前.tb_next
