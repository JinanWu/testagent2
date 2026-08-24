"""CP5 敏感命中 normalized authority 的持久化結構契約測試。"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.資料庫結構契約 import (
    資料庫結構契約錯誤,
    資料庫結構指紋,
    計算資料庫結構指紋,
    遷移帳本,
    驗證資料庫結構,
)


遷移路徑 = Path("繁中代理/發布介面/遷移/0015_建立敏感命中帳本.sql")
權威資料表 = "invocation_sensitive_hits"
預期欄位 = (
    "id",
    "invocation_id",
    "tool_call_id",
    "target_type",
    "detector_type",
    "json_path",
    "start_offset",
    "end_offset",
    "audit_event_id",
    "detected_at",
)
固定目標 = ("input", "metadata", "response_data", "tool_arguments", "tool_result")
禁止欄位字詞 = (
    "value",
    "snippet",
    "raw",
    "payload",
    "ciphertext",
    "secret",
    "hash",
    "credential",
    "session",
    "message",
)


def _建立資料庫(路徑: Path) -> None:
    """建立已完整套用發布遷移的隔離資料庫。

    描述：對指定暫存路徑執行 fresh migration，並確認第二次套用為無操作。
    參數：``路徑`` 是 pytest 暫存目錄內尚未存在的 SQLite 檔案路徑。
    返回值：無；遷移版本或冪等性不符時由斷言失敗。
    """
    assert 初始化發布介面資料庫(路徑) == tuple(range(1, 17))
    assert 初始化發布介面資料庫(路徑) == ()


def _建立安全父資料(連線: sqlite3.Connection) -> None:
    """建立敏感命中 FK 測試所需且不含命中原值的父資料。

    描述：加入同端點的兩個 invocation、單一工具呼叫與安全 audit identities。
    參數：``連線`` 是已啟用 foreign keys 的可寫 SQLite 連線。
    返回值：無；資料列直接留在呼叫端交易中。
    """
    連線.execute("INSERT INTO service_accounts VALUES('sa-main',1,NULL)")
    連線.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
        "current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) "
        "VALUES('ep-main','owner-main','sa-main','safe-endpoint','active',NULL,1,1,60,60)"
    )
    連線.execute(
        "INSERT INTO published_endpoint_versions VALUES("
        "'ver-main','ep-main',1,'safe requirement','safe prompt','[]','[]','{}','revision',"
        "'{}','{}','{}',NULL,'{}',0,'owner-main',1)"
    )
    for 呼叫識別碼, 請求識別碼 in (("inv-main", "req-main"), ("inv-other", "req-other")):
        連線.execute(
            "INSERT INTO endpoint_invocations("
            "id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at"
            ") VALUES(?, 'ep-main','ver-main',?,'succeeded','{}',2)",
            (呼叫識別碼, 請求識別碼),
        )
    連線.execute(
        "INSERT INTO endpoint_tool_calls("
        "id,invocation_id,sequence_number,tool_name,arguments_json,outcome,result_json,created_at"
        ") VALUES('tool-main','inv-main',1,'safe_tool','{}','success','{}',3)"
    )
    for 稽核識別碼, 呼叫識別碼 in (
        ("audit-main", "inv-main"),
        ("audit-tool", "inv-main"),
        ("audit-other", "inv-other"),
    ):
        _新增稽核(連線, 稽核識別碼, 呼叫識別碼)


def _新增稽核(連線: sqlite3.Connection, 稽核識別碼: str, 呼叫識別碼: str) -> None:
    """新增一筆只含安全 identity 與空 metadata 的 invocation audit。

    描述：建立可供 hit FK 與 invocation consistency 驗證的 append-only audit row。
    參數：``連線`` 是可寫連線；兩個識別碼分別指定 audit 與 invocation identity。
    返回值：無；資料列直接加入呼叫端交易。
    """
    連線.execute(
        "INSERT INTO audit_events("
        "id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,"
        "request_id,endpoint_id,invocation_id,metadata_json,created_at"
        ") VALUES(?,?,4,'published_api.location_detected','success','system',NULL,"
        "'invocation',?,NULL,'ep-main',?,'{}',4)",
        (稽核識別碼, 稽核識別碼, 呼叫識別碼, 呼叫識別碼),
    )


def _新增命中(
    連線: sqlite3.Connection,
    *,
    識別碼: str,
    呼叫識別碼: str = "inv-main",
    工具識別碼: str | None = None,
    目標: str = "input",
    偵測器: str = "format_detector",
    路徑: str = "/field",
    開始: int = 1,
    結束: int = 3,
    稽核識別碼: str = "audit-main",
) -> None:
    """新增一筆 location-only 敏感命中測試列。

    描述：只寫 identity、target、detector、RFC 6901 path、offset、audit 與時間。
    參數：``連線`` 與具名參數完整指定 normalized hit 欄位，不接受命中原值。
    返回值：無；資料列直接加入呼叫端交易。
    """
    連線.execute(
        "INSERT INTO invocation_sensitive_hits("
        "id,invocation_id,tool_call_id,target_type,detector_type,json_path,start_offset,"
        "end_offset,audit_event_id,detected_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,5)",
        (識別碼, 呼叫識別碼, 工具識別碼, 目標, 偵測器, 路徑, 開始, 結束, 稽核識別碼),
    )


def _開啟含父資料資料庫(路徑: Path) -> sqlite3.Connection:
    """建立資料庫並回傳含安全父資料的外鍵連線。

    描述：組合 fresh migration、foreign-key 啟用與共用安全父資料建置。
    參數：``路徑`` 是 pytest 暫存 SQLite 檔案路徑。
    返回值：由測試負責關閉的 ``sqlite3.Connection``。
    """
    _建立資料庫(路徑)
    連線 = sqlite3.connect(路徑)
    連線.execute("PRAGMA foreign_keys=ON")
    _建立安全父資料(連線)
    return 連線


def test_0015_manifest_fresh_apply_twice與唯一authority(tmp_path: Path) -> None:
    """固定 0015 manifest、fresh/apply-twice 與唯一資料表名稱。

    描述：確認 migration 檔、帳本尾端與 normalized authority 只有一份。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；契約由斷言表示。
    """
    assert 遷移路徑.is_file()
    assert [(項目.版本, 項目.名稱) for 項目 in 載入發布介面遷移()][14] == (
        15,
        遷移路徑.name,
    )
    assert 遷移帳本[14] == (15, 遷移路徑.name)
    db = tmp_path / "fresh.sqlite3"
    _建立資料庫(db)
    with sqlite3.connect(db) as 連線:
        表名 = {
            列[0]
            for 列 in 連線.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert 權威資料表 in 表名
        assert "sensitive_hits" not in 表名


def test_0015_exact_columns與schema_SQL無禁止欄位(tmp_path: Path) -> None:
    """固定 location-only 欄位並排除可保存原值或額外 identity 的欄位。

    描述：同時檢查 table inventory 與 0015 SQL 的 identifier token。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；發現額外或禁止欄位時由斷言失敗。
    """
    db = tmp_path / "columns.sqlite3"
    _建立資料庫(db)
    with sqlite3.connect(db) as 連線:
        assert tuple(列[1] for 列 in 連線.execute(
            "PRAGMA table_info(invocation_sensitive_hits)"
        )) == 預期欄位
        物件SQL = "\n".join(
            列[0]
            for 列 in 連線.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL ORDER BY type,name",
                (權威資料表,),
            )
        ).lower()
    assert 物件SQL
    assert 遷移路徑.read_text(encoding="utf-8").lower().count("create table") == 1
    for 字詞 in 禁止欄位字詞:
        assert re.search(rf"(?<![a-z0-9_]){re.escape(字詞)}(?![a-z0-9_])", 物件SQL) is None


def test_0015_五種target與RFC6901_offset_CHECK(tmp_path: Path) -> None:
    """固定五種 target、tool nullable 規則、RFC 6901 path 與 offset checks。

    描述：逐一接受五個法定 target，並拒絕額外 target、非法 pointer 與反向範圍。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；所有合法與拒絕案例由 SQLite constraint 結果表示。
    """
    db = tmp_path / "checks.sqlite3"
    with _開啟含父資料資料庫(db) as 連線:
        for 索引, 目標 in enumerate(固定目標):
            稽核識別碼 = f"audit-target-{索引}"
            _新增稽核(連線, 稽核識別碼, "inv-main")
            _新增命中(
                連線,
                識別碼=f"hit-target-{索引}",
                工具識別碼="tool-main" if 目標.startswith("tool_") else None,
                目標=目標,
                路徑="" if 索引 == 0 else "/field/~0/~1",
                開始=索引,
                結束=索引 + 1,
                稽核識別碼=稽核識別碼,
            )
        for 索引, (目標, 工具識別碼, 路徑, 開始, 結束) in enumerate((
            ("other", None, "/field", 1, 2),
            ("input", "tool-main", "/field", 1, 2),
            ("tool_result", None, "/field", 1, 2),
            ("input", None, "field", 1, 2),
            ("input", None, "/field/~2", 1, 2),
            ("input", None, "/field", 2, 2),
        )):
            稽核識別碼 = f"audit-invalid-{索引}"
            _新增稽核(連線, 稽核識別碼, "inv-main")
            with pytest.raises(sqlite3.IntegrityError):
                _新增命中(
                    連線,
                    識別碼=f"hit-invalid-{索引}",
                    工具識別碼=工具識別碼,
                    目標=目標,
                    路徑=路徑,
                    開始=開始,
                    結束=結束,
                    稽核識別碼=稽核識別碼,
                )


def test_0015_invocation_audit_tool_FK與一致性(tmp_path: Path) -> None:
    """固定 invocation、audit 與 nullable tool FK 及同 invocation 一致性。

    描述：檢查 FK inventory，並拒絕不存在或跨 invocation 的 parent identity。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；外鍵與 trigger 契約由斷言及預期 IntegrityError 表示。
    """
    db = tmp_path / "foreign-keys.sqlite3"
    with _開啟含父資料資料庫(db) as 連線:
        外鍵 = 連線.execute("PRAGMA foreign_key_list(invocation_sensitive_hits)").fetchall()
        assert {(列[2], 列[3], 列[4], 列[6]) for 列 in 外鍵} == {
            ("endpoint_invocations", "invocation_id", "id", "RESTRICT"),
            ("endpoint_tool_calls", "tool_call_id", "id", "RESTRICT"),
            ("endpoint_tool_calls", "invocation_id", "invocation_id", "RESTRICT"),
            ("audit_events", "audit_event_id", "id", "CASCADE"),
        }
        with pytest.raises(sqlite3.IntegrityError):
            _新增命中(連線, 識別碼="hit-no-invocation", 呼叫識別碼="inv-missing")
        with pytest.raises(sqlite3.IntegrityError):
            _新增命中(連線, 識別碼="hit-no-audit", 稽核識別碼="audit-missing")
        with pytest.raises(sqlite3.IntegrityError, match="audit invocation mismatch"):
            _新增命中(連線, 識別碼="hit-cross-audit", 稽核識別碼="audit-other")
        with pytest.raises(sqlite3.IntegrityError):
            _新增命中(
                連線,
                識別碼="hit-cross-tool",
                呼叫識別碼="inv-other",
                工具識別碼="tool-main",
                目標="tool_arguments",
                稽核識別碼="audit-other",
            )


def test_0015_nullable_tool_hit_identity使用兩個partial_unique_indexes(tmp_path: Path) -> None:
    """固定 nullable tool_call_id 下仍唯一的 hit identity。

    描述：檢查兩個 partial unique indexes，並分別拒絕 invocation 與 tool hit 重複。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；index inventory 與 duplicate constraint 由斷言表示。
    """
    db = tmp_path / "unique.sqlite3"
    with _開啟含父資料資料庫(db) as 連線:
        索引 = {列[1]: 列 for 列 in 連線.execute(
            "PRAGMA index_list(invocation_sensitive_hits)"
        )}
        assert 索引["uq_invocation_sensitive_hits_without_tool"][2:5] == (1, "c", 1)
        assert 索引["uq_invocation_sensitive_hits_with_tool"][2:5] == (1, "c", 1)
        _新增命中(連線, 識別碼="hit-main")
        _新增稽核(連線, "audit-main-duplicate", "inv-main")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _新增命中(
                連線,
                識別碼="hit-main-duplicate",
                稽核識別碼="audit-main-duplicate",
            )
        _新增命中(
            連線,
            識別碼="hit-tool",
            工具識別碼="tool-main",
            目標="tool_result",
            稽核識別碼="audit-tool",
        )
        _新增稽核(連線, "audit-tool-duplicate", "inv-main")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _新增命中(
                連線,
                識別碼="hit-tool-duplicate",
                工具識別碼="tool-main",
                目標="tool_result",
                稽核識別碼="audit-tool-duplicate",
            )


def test_0015_audit一對一_append_only與固定管理排序索引(tmp_path: Path) -> None:
    """固定一 hit 對一 audit、append-only 與 deterministic admin sort index。

    描述：拒絕重用 audit event、更新及刪除，並驗證管理排序索引欄序。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；所有治理語意以 schema inventory 與 IntegrityError 表示。
    """
    db = tmp_path / "append-only.sqlite3"
    with _開啟含父資料資料庫(db) as 連線:
        _新增命中(連線, 識別碼="hit-main")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _新增命中(
                連線,
                識別碼="hit-reused-audit",
                路徑="/other",
                稽核識別碼="audit-main",
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            連線.execute(
                "UPDATE invocation_sensitive_hits SET detected_at=6 WHERE id='hit-main'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            連線.execute("DELETE FROM invocation_sensitive_hits WHERE id='hit-main'")
        連線.execute("DROP TRIGGER audit_events_no_delete")
        連線.execute("DELETE FROM audit_events WHERE id='audit-main'")
        assert 連線.execute(
            "SELECT COUNT(*) FROM invocation_sensitive_hits WHERE id='hit-main'"
        ).fetchone() == (0,)
        assert tuple(列[2] for 列 in 連線.execute(
            "PRAGMA index_info(idx_invocation_sensitive_hits_admin_sort)"
        )) == (
            "invocation_id",
            "target_type",
            "tool_call_id",
            "json_path",
            "start_offset",
            "end_offset",
            "detector_type",
            "id",
        )


def test_0015_restart只讀回location_authority(tmp_path: Path) -> None:
    """確認 restart 後 durable readback 仍只有 identity 與位置欄位。

    描述：提交一筆安全 location row，關閉連線後以新連線讀回 exact 欄位和值。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；restart 後的 exact tuple 由斷言固定。
    """
    db = tmp_path / "restart.sqlite3"
    with _開啟含父資料資料庫(db) as 連線:
        _新增命中(連線, 識別碼="hit-restart", 路徑="/nested/0")
        連線.commit()
    with sqlite3.connect(db) as 重新開啟:
        assert tuple(列[1] for 列 in 重新開啟.execute(
            "PRAGMA table_info(invocation_sensitive_hits)"
        )) == 預期欄位
        assert 重新開啟.execute(
            "SELECT id,invocation_id,tool_call_id,target_type,detector_type,json_path,"
            "start_offset,end_offset,audit_event_id,detected_at "
            "FROM invocation_sensitive_hits"
        ).fetchall() == [(
            "hit-restart",
            "inv-main",
            None,
            "input",
            "format_detector",
            "/nested/0",
            1,
            3,
            "audit-main",
            5.0,
        )]


def test_0015_schema_fingerprint與drift_fail_closed(tmp_path: Path) -> None:
    """固定 0015 後完整 schema fingerprint 並拒絕結構 drift。

    描述：先驗證 fresh schema，再加入未知物件確認權威驗證關閉失敗。
    參數：``tmp_path`` 是 pytest 提供的隔離暫存目錄。
    返回值：無；指紋相等與 drift 例外由斷言表示。
    """
    db = tmp_path / "fingerprint.sqlite3"
    _建立資料庫(db)
    with sqlite3.connect(db) as 連線:
        assert 計算資料庫結構指紋(連線) == 資料庫結構指紋
        驗證資料庫結構(連線)
        連線.execute("CREATE TABLE unexpected_sensitive_hit_authority(id INTEGER)")
        with pytest.raises(資料庫結構契約錯誤):
            驗證資料庫結構(連線)
