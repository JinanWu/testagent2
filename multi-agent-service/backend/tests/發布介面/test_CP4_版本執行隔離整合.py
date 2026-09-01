"""CP4-RUNTIME R11 actual-module 建立期 pre-model 整合矩陣。"""

import json
import os
import sqlite3

import pytest

from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器, 套件發布錯誤
from 繁中代理.發布介面.技能套件.載入器 import 已發布技能套件載入器
from 繁中代理.發布介面.技能套件.儲存庫 import 套件收據儲存庫
from 繁中代理.發布介面.執行期.快照儲存庫 import SQLite發布快照儲存庫
from 繁中代理.發布介面.執行期.工具版本庫 import 計算工具修訂摘要
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.執行期.執行器 import 發布執行請求, 建立發布執行器


def _正規(值):
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _工具摘要(name, revision, description, parameters_json):
    return 計算工具修訂摘要(
        name=name, revision=revision, description=description,
        parameters=json.loads(parameters_json),
    )


class _模型收集器:
    def __init__(self, 標記="成功"):
        self.標記, self.calls = 標記, []

    def 產生發布回應(self, **參數):
        self.calls.append(參數)
        return 模型回應快照(self.標記, "stop", {"total": 1}, [])


def _解除不可變觸發器(連線):
    for (名稱,) in 連線.execute("SELECT name FROM sqlite_master WHERE type='trigger'"):
        連線.execute(f'DROP TRIGGER "{名稱}"')


@pytest.fixture
def 整合環境(tmp_path):
    發布根 = tmp_path / "bundles"
    技能表 = {}
    for 名稱 in ("alpha", "beta"):
        根 = tmp_path / f"source-{名稱}"
        (根 / "references").mkdir(parents=True)
        (根 / "scripts").mkdir()
        (根 / "assets").mkdir()
        (根 / "SKILL.md").write_text(f"{名稱}-允許提示", encoding="utf-8")
        (根 / "references" / "guide.md").write_text(f"{名稱}-參考允許", encoding="utf-8")
        (根 / "scripts" / "run.py").write_text(f"{名稱}-SCRIPT-禁止", encoding="utf-8")
        (根 / "assets" / "secret.txt").write_text(f"{名稱}-ASSET-禁止", encoding="utf-8")
        技能表[名稱] = 根
    收據 = 技能套件發布器(發布根).發布(
        套件識別碼="bundle-1", 端點識別碼="ep-1", 端點版本識別碼="ver-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表=技能表,
    )
    路徑 = tmp_path / "runtime.sqlite3"
    初始化發布介面資料庫(路徑)
    工具結構 = {"lookup": {"revision": "rev-1", "description": "固定工具",
                              "parameters": {"type": "object"}}}
    模型設定 = {"provider": "fake", "model": "model-1", "temperature": 0.0,
                "max_tokens": 20, "timeout_seconds": 3.0,
                "structured_output": False, "schema_retry_count": 1}
    清單原文 = (收據.路徑 / "manifest.json").read_text(encoding="utf-8")
    with sqlite3.connect(路徑) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        連線.execute("INSERT INTO service_accounts VALUES('sa-2',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-1", "owner-1", "sa-1", "exact", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-2", "owner-2", "sa-2", "fallback-trap", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-1", "ep-1", 1, "需求", "固定系統提示", "[]", _正規(["lookup"]),
             _正規(工具結構), "release-1", _正規(模型設定), "{}", 清單原文,
             None, "null", 0, "owner-1", 1),
        )
        套件收據儲存庫(連線).新增(版本識別碼="ver-1", 收據=收據, 發布時間=2.0)
    工具庫 = 工具發布庫()
    工具庫.登錄發布(工具發布描述("release-1", (工具發布註冊(
        "rev-1", 工具定義("lookup", "固定工具", {"type": "object"}, lambda _: "release-1"),
    ),)))
    模型 = _模型收集器()
    return 路徑, 發布根, 收據, 工具庫, 模型


def _建立(環境):
    路徑, 發布根, _, 工具庫, 模型 = 環境
    快照庫 = SQLite發布快照儲存庫(路徑, _工具摘要)
    快照 = 快照庫.取得發布執行快照("ver-1")
    發布版 = 工具庫.取得發布(快照.tool_handler_release)
    載入器 = 已發布技能套件載入器(發布根, 快照庫)
    return 建立發布執行器(
        endpoint_version_id="ver-1", service_account_id="sa-1",
        發布快照提供者=快照, 服務帳戶載入器=快照庫,
        技能套件載入器=載入器, 工具修訂提供者=發布版,
        模型供應商註冊表={"fake": 模型},
    )


def test_actual_modules_exact版本組裝且模型恰呼叫一次(整合環境):
    _, _, _, _, 模型 = 整合環境
    回應 = _建立(整合環境).執行(發布執行請求({"question": "R11"}))
    assert 回應.text == "成功" and len(模型.calls) == 1
    呼叫 = 模型.calls[0]
    提示 = 呼叫["messages"][0]["content"]
    assert all(文字 in 提示 for 文字 in (
        "固定系統提示", "alpha-允許提示", "beta-允許提示", "alpha-參考允許", "beta-參考允許",
    ))
    assert "SCRIPT-禁止" not in 提示 and "ASSET-禁止" not in 提示
    assert 呼叫["tools"][0]["function"]["name"] == "lookup"


def test_actual_publisher接受4000001位元組並由direct_loader產生runtime_DTO(tmp_path):
    發布根 = tmp_path / "budget-bundles"
    技能根 = tmp_path / "budget-source"
    技能根.mkdir()
    for 名稱, 大小 in (("SKILL.md", 1024 * 1024), ("a.bin", 1024 * 1024),
                     ("b.bin", 1024 * 1024), ("c.bin", 854_273)):
        (技能根 / 名稱).write_bytes(b"x" * 大小)
    收據 = 技能套件發布器(發布根).發布(
        套件識別碼="bundle-budget", 端點識別碼="ep-budget",
        端點版本識別碼="ver-budget", 版本號碼=1, 建立時間=1.0,
        建立者識別碼="owner-budget", 技能表={"budget": 技能根},
    )
    assert 收據.總位元組數 == 4_000_001

    路徑 = tmp_path / "budget.sqlite3"
    初始化發布介面資料庫(路徑)
    模型設定 = {"provider": "fake", "model": "model-1", "temperature": 0.0,
                "max_tokens": 20, "timeout_seconds": 3.0,
                "structured_output": False, "schema_retry_count": 1}
    with sqlite3.connect(路徑) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-budget',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-budget", "owner-budget", "sa-budget", "budget", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-budget", "ep-budget", 1, "需求", "固定提示", "[]", "[]", "{}",
             "release-1", _正規(模型設定), "{}",
             (收據.路徑 / "manifest.json").read_text(encoding="utf-8"),
             None, "null", 0, "owner-budget", 1),
        )
        套件收據儲存庫(連線).新增(版本識別碼="ver-budget", 收據=收據, 發布時間=2.0)
    快照庫 = SQLite發布快照儲存庫(路徑, _工具摘要)
    快照 = 已發布技能套件載入器(發布根, 快照庫).載入技能套件快照(
        "ver-budget", 收據.套件雜湊, 收據.清單參照, "endpoint_version_snapshot",
    )
    assert sum(len(檔案.content) for 檔案 in 快照.files) == 4_000_001

    超限根 = tmp_path / "over-source"
    超限根.mkdir()
    for 名稱 in ("SKILL.md", "a.bin", "b.bin", "c.bin"):
        (超限根 / 名稱).write_bytes(b"y" * (1024 * 1024))
    (超限根 / "d.bin").write_bytes(b"z")
    with pytest.raises(套件發布錯誤, match="^技能套件發布失敗$"):
        技能套件發布器(tmp_path / "over-bundles").發布(
            套件識別碼="bundle-over", 端點識別碼="ep-over",
            端點版本識別碼="ver-over", 版本號碼=1, 建立時間=1.0,
            建立者識別碼="owner-over", 技能表={"over": 超限根},
        )


@pytest.mark.parametrize("破壞,預期", [
    ("version_missing", "發布快照不可用"), ("disabled", "發布快照不可用"),
    ("cross_sa", "發布快照不可用"), ("bundle_missing", "發布快照不可用"),
    ("bundle_ref", "發布快照不可用"), ("manifest_digest", "發布快照不可用"),
    ("bundle_hash", "發布快照不可用"), ("file_digest", "發布執行期不可用"),
    ("tool_release", "發布快照不可用"), ("tool_revision", "發布快照不可用"),
    ("tool_digest", "發布快照不可用"), ("model_config", "發布快照不可用"),
    ("response_schema", "發布快照不可用"),
])
def test_pre_model_zero_call整合矩陣(整合環境, 破壞, 預期):
    路徑, _, 收據, _, 模型 = 整合環境
    if 破壞 == "file_digest":
        目標 = 收據.路徑 / "alpha" / "scripts" / "run.py"
        os.chmod(目標, 0o600)
        目標.write_text("已竄改", encoding="utf-8")
    else:
        with sqlite3.connect(路徑) as 連線:
            _解除不可變觸發器(連線)
            if 破壞 == "version_missing":
                連線.execute("DELETE FROM published_endpoint_versions WHERE id='ver-1'")
            elif 破壞 == "disabled":
                連線.execute("UPDATE published_endpoints SET status='disabled' WHERE id='ep-1'")
            elif 破壞 == "cross_sa":
                連線.execute("UPDATE published_endpoint_versions SET endpoint_id='ep-2' WHERE id='ver-1'")
            elif 破壞 == "bundle_missing":
                連線.execute("DELETE FROM published_skill_bundles WHERE version_id='ver-1'")
            elif 破壞 == "bundle_ref":
                連線.execute("UPDATE published_skill_bundles SET manifest_reference='bundle-x/manifest.json'")
            elif 破壞 in ("manifest_digest", "bundle_hash"):
                欄 = "manifest_digest" if 破壞 == "manifest_digest" else "bundle_hash"
                連線.execute(f"UPDATE published_skill_bundles SET {欄}=?", ("0" * 64,))
            elif 破壞 == "tool_release":
                連線.execute("UPDATE published_endpoint_versions SET tool_runtime_revision='release-missing'")
            elif 破壞 in ("tool_revision", "tool_digest"):
                結構 = {"lookup": {"revision": "rev-missing" if 破壞 == "tool_revision" else "rev-1",
                                     "description": "漂移" if 破壞 == "tool_digest" else "固定工具",
                                     "parameters": {"type": "object"}}}
                連線.execute("UPDATE published_endpoint_versions SET tool_schema_snapshot_json=?", (_正規(結構),))
            elif 破壞 == "model_config":
                設定 = {"provider": "missing", "model": "m", "temperature": 0.0,
                        "max_tokens": 20, "timeout_seconds": 3.0,
                        "structured_output": False, "schema_retry_count": 1}
                連線.execute("UPDATE published_endpoint_versions SET model_config_snapshot_json=?", (_正規(設定),))
            elif 破壞 == "response_schema":
                設定 = {"provider": "fake", "model": "m", "temperature": 0.0,
                        "max_tokens": 20, "timeout_seconds": 3.0,
                        "structured_output": True, "schema_retry_count": 1}
                連線.execute("UPDATE published_endpoint_versions SET model_config_snapshot_json=?,response_schema_json=?",
                             (_正規(設定), _正規({"type": 7})))
    with pytest.raises(Exception, match=f"^{預期}$"):
        _建立(整合環境)
    assert 模型.calls == []
