"""SQLite exact-version 發布快照儲存庫整合與敵對資料測試。"""
import hashlib
import json
import sqlite3

import pytest

import 繁中代理.發布介面.執行期.快照儲存庫 as 模組
from 繁中代理.發布介面.執行期.快照儲存庫 import (
    SQLite發布快照儲存庫, 技能套件定位, 發布快照儲存庫錯誤,
)
from 繁中代理.發布介面.技能套件.載入器 import 技能套件定位 as 載入器技能套件定位
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.執行期.執行器 import 發布執行快照
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext


def _正規(值):
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _工具摘要(name, revision, description, parameters_json):
    投影 = {"name": name, "revision": revision, "description": description,
          "parameters": json.loads(parameters_json)}
    return hashlib.sha256(_正規(投影).encode()).hexdigest()


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "snapshot.sqlite3"
    初始化發布介面資料庫(路徑)
    工具 = {"lookup": {"revision": "rev-1", "description": "說明",
                         "parameters": {"type": "object"}}}
    模型 = {"provider": "fake", "model": "m", "temperature": 0.0,
            "max_tokens": 10, "timeout_seconds": 3.0,
            "structured_output": False, "schema_retry_count": 1}
    with sqlite3.connect(路徑) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) "
            "VALUES('ep-1','owner-1','sa-1','slug','active',NULL,1,1,60,60)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-1", "ep-1", 1, "需求", "系統提示", "[]", _正規(["lookup"]),
             _正規(工具), "release-1", _正規(模型), "{}", "{}", None, "null", 0,
             "owner-1", 1),
        )
        連線.execute(
            "INSERT INTO published_skill_bundles VALUES(?,?,?,?,?,?,?,?,?)",
            ("bundle-1", "ver-1", "bundle-1/manifest.json", "a" * 64, "b" * 64, 12,
             "published", 2, None),
        )
    return 路徑


def test_三個介面共用exact_authority並重建完整DTO(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    庫 = SQLite發布快照儲存庫(路徑, _工具摘要)
    快照 = 庫.取得發布執行快照("ver-1")
    上下文 = 庫.載入服務帳戶上下文("sa-1", "ver-1", "endpoint_version_snapshot")
    定位 = 庫.取得技能套件定位("ver-1")

    assert type(快照) is 發布執行快照
    assert (快照.version_id, 快照.endpoint_id, 快照.service_account_id) == ("ver-1", "ep-1", "sa-1")
    assert 快照.tool_snapshot[0].digest == _工具摘要(
        "lookup", "rev-1", "說明", _正規({"type": "object"})
    )
    assert 快照.response_schema is None and 快照.model_config.structured_output is False
    assert type(上下文) is ServiceAccountContext
    assert 上下文.permission_snapshot_digest == 快照.permission_snapshot_digest
    assert 上下文.allowed_tools == ("lookup",)
    assert 技能套件定位 is 載入器技能套件定位
    assert type(定位) is 載入器技能套件定位
    assert 定位 == 載入器技能套件定位(
        version_id="ver-1", bundle_id="bundle-1",
        manifest_reference="bundle-1/manifest.json", manifest_digest="a" * 64,
        bundle_hash="b" * 64, total_bytes=12,
    )


def test_SQL只依exact_version且schema驗證與查詢在同一BEGIN交易(tmp_path, monkeypatch):
    路徑 = _建立資料庫(tmp_path)
    事件 = []
    原驗證 = 模組.驗證資料庫結構

    def 驗證(連線):
        事件.append(("schema", 連線.in_transaction))
        return 原驗證(連線)

    monkeypatch.setattr(模組, "驗證資料庫結構", 驗證)
    SQLite發布快照儲存庫(路徑, _工具摘要).取得技能套件定位("ver-1")
    SQL = 模組._快照查詢.lower()
    assert 事件 == [("schema", True)]
    assert "where v.id=?" in SQL and "join" in SQL and "limit 2" in SQL
    assert all(禁止 not in SQL for 禁止 in ("current", "latest", "slug", "max("))


def test_無效識別與來源在開連線前固定拒絕(tmp_path):
    呼叫 = []
    def 工廠(*參數, **關鍵字):
        呼叫.append((參數, 關鍵字))
        raise AssertionError
    庫 = SQLite發布快照儲存庫(tmp_path / "missing", _工具摘要, 工廠)
    for 動作 in (
        lambda: 庫.取得發布執行快照("../bad"),
        lambda: 庫.載入服務帳戶上下文("sa-1", "ver-1", "owner_memory"),
    ):
        with pytest.raises(發布快照儲存庫錯誤, match="^發布快照不可用$") as 錯誤:
            動作()
        assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert 呼叫 == []


@pytest.mark.parametrize("欄位,值", [
    ("status", "disabled"), ("disabled_at", 3), ("state", "staging"),
    ("allowed_tools_json", '["lookup","lookup"]'),
    ("model_config_snapshot_json", '{"provider":"fake"}'),
    ("response_schema_json", "NaN"),
])
def test_敵對狀態型別與JSON全部固定拒絕(tmp_path, 欄位, 值):
    路徑 = _建立資料庫(tmp_path)
    表 = "published_endpoints" if 欄位 == "status" else "service_accounts" if 欄位 == "disabled_at" else \
         "published_skill_bundles" if 欄位 == "state" else "published_endpoint_versions"
    with sqlite3.connect(路徑) as 連線:
        if 表 == "published_endpoint_versions":
            連線.execute("DROP TRIGGER published_endpoint_versions_no_update")
        elif 表 == "published_skill_bundles":
            連線.execute("DROP TRIGGER published_skill_bundles_no_update")
        連線.execute(f"PRAGMA ignore_check_constraints=ON")
        連線.execute(f"UPDATE {表} SET {欄位}=?", (值,))
    with pytest.raises(發布快照儲存庫錯誤, match="^發布快照不可用$"):
        SQLite發布快照儲存庫(路徑, _工具摘要).取得發布執行快照("ver-1")


def test_missing_exact_version與工具摘要helper失敗皆固定化(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    with pytest.raises(發布快照儲存庫錯誤, match="^發布快照不可用$"):
        SQLite發布快照儲存庫(路徑, _工具摘要).取得發布執行快照("ver-2")
    def 失敗helper(*_):
        raise RuntimeError("秘密")
    with pytest.raises(發布快照儲存庫錯誤, match="^發布快照不可用$") as 錯誤:
        SQLite發布快照儲存庫(路徑, 失敗helper).取得發布執行快照("ver-1")
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
