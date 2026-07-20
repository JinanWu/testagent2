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
