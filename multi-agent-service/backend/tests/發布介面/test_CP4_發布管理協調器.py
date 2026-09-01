"""CP4 production 發布管理協調器與真實 primitive 端對端測試。"""
from __future__ import annotations

import sqlite3
import threading
import json
import os
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
from 繁中代理.發布介面.規劃.端點發布 import SQLite端點發布服務, 端點發布耐久性未知
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務
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
        "system_prompt": 綱要["system_prompt"], "input_schema": 綱要["input_schema"],
        "response_schema": 綱要["response_schema"], "human_docs": 綱要["human_docs"],
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
    產生紀錄: dict[int, dict[str, str]] = {}
    鎖 = threading.Lock()

    def 識別碼(前綴: str) -> str:
        """為並行測試提供每種 graph identity 的原子序號。"""
        with 鎖:
            次數[前綴] = 次數.get(前綴, 0) + 1
            值 = f"{前綴}-{次數[前綴]}"
            產生紀錄.setdefault(threading.get_ident(), {})[前綴] = 值
            return 值

    def 未使用識別() -> str:
        """prepared P04 路徑不得呼叫 legacy 識別工廠。"""
        return "unused"

    端點服務 = SQLite端點發布服務(
        資料庫, 未使用識別, 未使用識別, 未使用識別, 未使用識別, lambda: 20.0,
    )
    版本服務 = SQLite版本配置服務(資料庫, 未使用識別, lambda: 20.0)
    協調器 = 技能套件協調器(套件根, 孤兒保留秒數=3600, 時鐘=lambda: 20.0)
    服務 = 發布管理協調器(
        草稿服務=草稿服務, 擁有者解析器=解析器,
        套件發布器物件=技能套件發布器(套件根), 套件協調器物件=協調器,
        端點發布服務=端點服務, 版本配置服務=版本服務,
        憑證封套=封套, 時鐘=lambda: 20.0,
        識別碼產生器=識別碼, 隨機位元組=lambda 長度: bytes(range(長度)),
    )
    return {
        "服務": 服務, "資料庫": 資料庫, "套件根": 套件根, "封套": 封套,
        "使用者庫": 使用者庫物件, "擁有者": 擁有者, "解析器": 解析器,
        "草稿服務": 草稿服務, "端點服務": 端點服務, "版本服務": 版本服務,
        "協調器": 協調器, "技能主檔": 技能 / "SKILL.md",
        "產生紀錄": 產生紀錄,
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
    assert (環境["套件根"] / "bundle-1" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-1").exists()
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
    assert type(第一筆) is 端點發布結果 and 第二筆 == 管理操作錯誤("status_conflict")
    連線 = sqlite3.connect(環境["資料庫"])
    assert 連線.execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)
    assert 連線.execute("SELECT count(*) FROM published_endpoint_versions").fetchone() == (1,)
    assert (環境["套件根"] / "bundle-1").is_dir()
    assert (環境["套件根"] / ".orphaned" / "bundle-2").is_dir()


def test_並行發布至多提交一個圖形且不覆寫贏家(tmp_path):
    """same-slug 兩執行緒須以正式 ID 工廠的實際 winner 串起八張圖形與 manifest。

    參數：``tmp_path`` 建立共享正式服務。回傳：無。例外：winner 任一欄位或關聯
    漂移、loser 任一正式產生 ID 進入 canonical DB／active bundle 即失敗。副作用：
    兩執行緒各經正式六次 ID 工廠、發布各自 bundle，再由 SQLite 唯一短名選出一方。
    """
    環境 = _建立環境(tmp_path)
    起跑 = threading.Barrier(3)
    結果列: list[tuple[int, object]] = []

    def 發布工作():
        """記錄執行緒身分，讓公開結果可回連該次正式 ID 工廠輸出。"""
        執行緒識別 = threading.get_ident()
        起跑.wait()
        結果列.append((執行緒識別, 環境["服務"].原子發布(
            擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
        )))

    執行緒們 = [threading.Thread(target=發布工作) for _ in range(2)]
    for 執行緒 in 執行緒們:
        執行緒.start()
    起跑.wait()
    for 執行緒 in 執行緒們:
        執行緒.join(10)
    assert all(not 執行緒.is_alive() for 執行緒 in 執行緒們)
    assert sum(type(項目[1]) is 端點發布結果 for 項目 in 結果列) == 1
    assert sum(type(項目[1]) is 管理操作錯誤 for 項目 in 結果列) == 1
    贏家執行緒, 贏家 = next(項目 for 項目 in 結果列 if type(項目[1]) is 端點發布結果)
    輸家執行緒, 輸家結果 = next(項目 for 項目 in 結果列 if type(項目[1]) is 管理操作錯誤)
    assert type(贏家) is 端點發布結果
    assert 輸家結果 == 管理操作錯誤("status_conflict")
    贏家ID = 環境["產生紀錄"][贏家執行緒]
    輸家ID = 環境["產生紀錄"][輸家執行緒]
    assert set(贏家ID) == set(輸家ID) == {
        "endpoint", "version", "credential", "account", "bundle", "audit",
    }
    assert 贏家.端點識別碼 == 贏家ID["endpoint"]
    assert 贏家.版本識別碼 == 贏家ID["version"]

    active = 環境["套件根"] / 贏家ID["bundle"]
    loser_active = 環境["套件根"] / 輸家ID["bundle"]
    loser_orphan = 環境["套件根"] / ".orphaned" / 輸家ID["bundle"]
    assert (active / "manifest.json").is_file()
    assert not loser_active.exists() and (loser_orphan / "manifest.json").is_file()
    manifest_bytes = (active / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    manifest_digest = __import__("hashlib").sha256(manifest_bytes).hexdigest()
    assert (
        manifest["bundle_id"], manifest["endpoint_id"], manifest["endpoint_version_id"],
        manifest["version_number"], manifest["created_at"], manifest["created_by_user_id"],
    ) == (
        贏家ID["bundle"], 贏家ID["endpoint"], 贏家ID["version"], 1, 20.0, 環境["擁有者"],
    )

    連線 = sqlite3.connect(環境["資料庫"])
    assert 連線.execute(
        "SELECT id,created_at,disabled_at FROM service_accounts"
    ).fetchone() == (贏家ID["account"], 20.0, None)
    assert 連線.execute(
        "SELECT id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds FROM published_endpoints"
    ).fetchone() == (
        贏家ID["endpoint"], 環境["擁有者"], 贏家ID["account"], "alpha-api", "active",
        贏家ID["version"], 20.0, 20.0, 60, 60,
    )
    version = 連線.execute(
        "SELECT id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,created_by_user_id,created_at FROM published_endpoint_versions"
    ).fetchone()
    assert version[:7] == (
        贏家ID["version"], 贏家ID["endpoint"], 1, "建立 Alpha API", "只根據技能回答",
        '["alpha"]', '["alpha-tool"]',
    )
    assert json.loads(version[7])["alpha-tool"]["revision"] == "revision-1"
    assert version[8:11] == (
        "release-1", '{"model":"published-default","temperature":0}', '{"max_attempts":1}',
    )
    snapshot_manifest = json.loads(version[11])
    assert snapshot_manifest["bundle_id"] == 贏家ID["bundle"]
    assert snapshot_manifest["manifest_reference"] == f'{贏家ID["bundle"]}/manifest.json'
    assert snapshot_manifest["manifest_digest"] == manifest_digest
    assert snapshot_manifest["sha256"] == manifest["bundle_hash"]
    assert version[12:] == (
        None,
        '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},"required":["answer"],"type":"object"}',
        0, 環境["擁有者"], 20.0,
    )
    assert 連線.execute(
        "SELECT draft_id,endpoint_id,consumed_at FROM published_draft_consumptions"
    ).fetchone() == ("draft-1", 贏家ID["endpoint"], 20.0)
    assert 連線.execute(
        "SELECT version_id,publication_source,prompt_changed,skills_changed,tools_changed,model_changed,docs_changed FROM published_endpoint_version_metadata"
    ).fetchone() == (贏家ID["version"], "initial_draft", 0, 0, 0, 0, 0)
    credential = 連線.execute(
        "SELECT id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,rate_limit_requests,created_by_user_id,revision FROM endpoint_credentials"
    ).fetchone()
    assert credential[:5] == (
        贏家ID["credential"], 贏家ID["endpoint"], "初始憑證", "呼叫已發布端點", 1,
    )
    assert credential[5] == b"N" * 12 and type(credential[6]) is bytes and len(credential[6]) > 0
    assert credential[7:19] == (
        __import__("hashlib").sha256(贏家.初始API金鑰.encode()).hexdigest(),
        贏家.初始API金鑰[:12], 贏家.初始API金鑰[-4:], 31_536_020.0, None,
        20.0, 20.0, None, "[]", 30, 環境["擁有者"], 0,
    )
    assert 連線.execute(
        "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at FROM published_skill_bundles"
    ).fetchone() == (
        贏家ID["bundle"], 贏家ID["version"], f'{贏家ID["bundle"]}/manifest.json',
        manifest_digest, manifest["bundle_hash"], manifest["total_bytes"], "published", 20.0, None,
    )
    audit = 連線.execute(
        "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at FROM audit_events"
    ).fetchone()
    assert audit[:12] == (
        贏家ID["audit"], 贏家ID["audit"], 20.0, "endpoint_published", "success", "user",
        環境["擁有者"], "published_endpoint", 贏家ID["endpoint"], None,
        贏家ID["endpoint"], None,
    )
    assert json.loads(audit[12]) == {
        "bundle_hash": manifest["bundle_hash"], "bundle_id": 贏家ID["bundle"],
        "credential_id": 贏家ID["credential"], "service_account_id": 贏家ID["account"],
        "version_id": 贏家ID["version"], "version_number": 1,
    }
    assert audit[13] == 20.0

    assert 連線.execute(
        "SELECT 1 FROM service_accounts WHERE id=?", (輸家ID["account"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM published_endpoints WHERE id=?", (輸家ID["endpoint"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM published_endpoint_versions WHERE id=?", (輸家ID["version"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM endpoint_credentials WHERE id=?", (輸家ID["credential"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM published_skill_bundles WHERE bundle_id=?", (輸家ID["bundle"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM audit_events WHERE id=?", (輸家ID["audit"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM published_draft_consumptions WHERE endpoint_id=?", (輸家ID["endpoint"],)
    ).fetchone() is None
    assert 連線.execute(
        "SELECT 1 FROM published_endpoint_version_metadata WHERE version_id=?", (輸家ID["version"],)
    ).fetchone() is None


def test_P04提交durability_unknown不得orphan或刪除final(tmp_path, monkeypatch):
    """P04 專用 unknown 只回固定錯誤，final 留在 active 供 reconciliation。

    參數：``tmp_path`` 組裝真 publisher；``monkeypatch`` 只令 genuine P04 dependency 回報
    已分類結果並禁止 orphan。回傳：無。例外：分類錯誤、移動或刪除 final 即測試失敗。
    副作用：真實發布 bundle，但不寫 DB graph，並保留 active final。
    """
    環境 = _建立環境(tmp_path)

    def unknown(*_參數, **_選項):
        """模擬 P04 已完成 fresh readback 但仍無法判定 durability。"""
        raise 端點發布耐久性未知("端點發布耐久性未知")

    def 不得孤兒(_收據):
        """unknown 分支若嘗試 orphan 立即使測試失敗。"""
        raise AssertionError("durability unknown 不得 orphan")

    monkeypatch.setattr(環境["端點服務"], "發布已準備圖形", unknown)
    monkeypatch.setattr(環境["協調器"], "標記孤兒", 不得孤兒)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 結果 == 管理操作錯誤("internal")
    assert (環境["套件根"] / "bundle-1" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-1").exists()
    assert sqlite3.connect(環境["資料庫"]).execute(
        "SELECT count(*) FROM published_endpoints"
    ).fetchone() == (0,)


def test_COMMIT正常ack後canonical被替換仍回durability_unknown並保留final(tmp_path):
    """正常 COMMIT acknowledgement 也必須關閉 owner 後重驗原 inode 與完整圖形。

    參數：``tmp_path`` 組裝真實管理層、publisher 與 SQLite；回傳：無。
    例外：端點公開服務必須產生專用 durability unknown，管理公開邊界固定映射為
    ``internal``。副作用：connection factory 讓真 COMMIT 完成後以空資料庫原子替換
    canonical path 並正常回傳；管理層必須保留已發布 final 供後續 reconciliation。
    """
    環境 = _建立環境(tmp_path)
    替代資料庫 = tmp_path / "empty-replacement.sqlite3"
    初始化發布介面資料庫(替代資料庫)
    已正常確認: list[bool] = []

    class 正常確認後替換連線(sqlite3.Connection):
        """完成真 COMMIT、替換 canonical path，然後正常回傳 acknowledgement。"""

        def execute(self, sql, parameters=()):
            結果 = super().execute(sql, parameters)
            if sql == "COMMIT":
                os.replace(替代資料庫, 環境["資料庫"])
                已正常確認.append(True)
            return 結果

    def connection_factory(*參數, **選項):
        """透過正式端點服務連線工廠建立 SQLite subclass。"""
        return sqlite3.connect(*參數, **選項, factory=正常確認後替換連線)

    環境["端點服務"]._連線工廠 = connection_factory
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 已正常確認 == [True]
    assert 結果 == 管理操作錯誤("internal")
    with sqlite3.connect(環境["資料庫"]) as fresh:
        assert fresh.execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)
    assert (環境["套件根"] / "bundle-1" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-1").exists()


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


def test_DB失敗orphan由下一次真實啟動協調依保留期清除(tmp_path, monkeypatch):
    """跨資源失敗留下的 orphan 必須能由新的 coordinator 在 restart 後安全清除。

    參數：``tmp_path`` 建立真實 publisher／DB；``monkeypatch`` 在 transaction 內撤銷 owner。
    回傳：無；以管理結果、零 DB graph、orphan move 與下一次啟動 cleanup assertions 表達。
    例外：發布失敗映射、隔離或 restart reconciliation 任一漂移時測試失敗。
    副作用：先建立並隔離 bundle，再以新的 coordinator 及 fresh DB connection 清除過期 orphan。
    """
    環境 = _建立環境(tmp_path)
    原解析 = 環境["解析器"].解析發布能力
    呼叫 = 0

    def transaction內撤銷(擁有者, 摘要):
        """第二次 authority lookup 前停用 owner，強制 FS 成功而 DB rollback。"""
        nonlocal 呼叫
        呼叫 += 1
        if 呼叫 == 2:
            環境["使用者庫"].設定使用者停用("owner", True)
        return 原解析(擁有者, 摘要)

    monkeypatch.setattr(環境["解析器"], "解析發布能力", transaction內撤銷)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    孤兒 = 環境["套件根"] / ".orphaned" / "bundle-1"
    assert 結果 == 管理操作錯誤("internal") and 孤兒.is_dir()
    with sqlite3.connect(環境["資料庫"]) as 連線:
        assert 連線.execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)
        restart協調器 = 技能套件協調器(
            環境["套件根"], 孤兒保留秒數=0, 時鐘=lambda: 21.0,
        )
        協調結果 = restart協調器.啟動協調(21.0, 連線)
    assert 協調結果.已刪除 == ("bundle-1",)
    assert not 孤兒.exists()


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


def test_真實publisher耐久性未知保留active_final且不得進入orphan(tmp_path):
    """parent fsync 不確定後必須保留 active final，不得以 orphan move 假裝 rollback。

    參數：``tmp_path`` 組裝真實 publisher／coordinator。回傳：無。例外：固定管理錯誤、
    active final、零 orphan 與零 DB graph 任一不符即測試失敗。副作用：在原子 rename
    後注入 parent fsync ordinary failure，留下供 reconciliation 的完整唯讀成果。
    """
    環境 = _建立環境(tmp_path)

    def 父同步失敗(名稱):
        """只在原子改名完成後注入 ordinary durability failure。"""
        if 名稱 == "parent_fsync":
            raise OSError("unknown")

    環境["服務"]._套件發布器 = 技能套件發布器(環境["套件根"], 失敗點=父同步失敗)
    結果 = 環境["服務"].原子發布(
        擁有者使用者識別碼=環境["擁有者"], 確認=_確認("draft-1"),
    )
    assert 結果 == 管理操作錯誤("internal")
    assert (環境["套件根"] / "bundle-1" / "manifest.json").is_file()
    assert not (環境["套件根"] / ".orphaned" / "bundle-1").exists()
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
