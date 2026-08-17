"""A20-01 server-owned 遮蔽命令與 durable 冪等 mapping 契約。"""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from inspect import signature
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Lock

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.資料庫結構契約 import 遷移帳本
from 繁中代理.發布介面.治理 import 遮蔽 as 遮蔽模組
from 繁中代理.發布介面.治理 import 稽核資料庫 as 稽核資料庫模組
from 繁中代理.發布介面.治理.保存期限 import SQLite保存清除服務
from 繁中代理.發布介面.治理.遮蔽 import (
    SQLite不可逆遮蔽服務,
    不可逆遮蔽錯誤,
    遮蔽路徑無效,
)
from 繁中代理.發布介面.治理.遮蔽命令 import (
    SQLite遮蔽命令服務,
    遮蔽命令冪等衝突,
    遮蔽命令目標不存在,
    遮蔽命令錯誤,
)


遷移路徑 = Path("繁中代理/發布介面/遷移/0016_建立遮蔽冪等命令.sql")
命令資料表 = "redaction_idempotency_commands"
預期欄位 = (
    "principal_id",
    "idempotency_key",
    "request_fingerprint",
    "redaction_id",
    "audit_event_id",
    "request_id",
    "endpoint_id",
    "invocation_id",
    "target_type",
    "target_row_id",
    "json_path",
    "reason",
    "first_seen_at",
)


def test_0016建立有界且不保存原文的遮蔽冪等命令schema(tmp_path: Path) -> None:
    """Fresh/apply-twice 後只存在 server identity、canonical request 與 first-seen metadata。"""
    assert 遷移路徑.is_file()
    assert [(項目.版本, 項目.名稱) for 項目 in 載入發布介面遷移()][-1] == (
        16,
        遷移路徑.name,
    )
    assert 遷移帳本[-1] == (16, 遷移路徑.name)

    資料庫 = tmp_path / "commands.sqlite3"
    assert 初始化發布介面資料庫(資料庫) == tuple(range(1, 17))
    assert 初始化發布介面資料庫(資料庫) == ()

    with sqlite3.connect(資料庫) as 連線:
        assert tuple(列[1] for 列 in 連線.execute(
            f"PRAGMA table_info({命令資料表})"
        )) == 預期欄位
        物件SQL = "\n".join(
            列[0]
            for 列 in 連線.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND sql IS NOT NULL ORDER BY type,name",
                (命令資料表,),
            )
        ).lower()

    assert 物件SQL
    for 禁止字詞 in (
        "original_payload",
        "original_value",
        "snippet",
        "ciphertext",
        "undo_token",
        "raw_payload",
    ):
        assert 禁止字詞 not in 物件SQL


def _建立命令資料庫(路徑: Path) -> None:
    """建立含一筆 endpoint/invocation 的 fresh 0016 DB。"""
    assert 初始化發布介面資料庫(路徑) == tuple(range(1, 17))
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts(id,created_at) VALUES('service-main',1)")
        連線.execute(
            "INSERT INTO published_endpoints("
            "id,owner_user_id,service_account_id,slug,status,created_at,updated_at"
            ") VALUES('endpoint-main','owner-main','service-main','main','active',1,1)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions("
            "id,endpoint_id,version_number,original_requirement_text,system_prompt,"
            "allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,"
            "model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,"
            "response_schema_json,schema_changed,created_by_user_id,created_at"
            ") VALUES('version-main','endpoint-main',1,'r','p','[]','[]','{}','runtime','{}','{}','{}',"
            "NULL,'{}',0,'owner-main',1)"
        )
        連線.execute(
            "INSERT INTO endpoint_invocations("
            "id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at"
            ") VALUES('invocation-main','endpoint-main','version-main','invoke-request','succeeded',"
            "'{\"private\":\"RAW_A20_02\"}',2)"
        )


def _命令參數(**覆寫):
    """回傳不含 admin flag、internal IDs、time 或原值的 public command arguments。"""
    參數 = {
        "管理員識別碼": "admin-main",
        "冪等鍵": "action-key-1",
        "端點識別碼": "endpoint-main",
        "呼叫識別碼": "invocation-main",
        "目標類型": "invocation_input",
        "目標列識別碼": "invocation-main",
        "JSON路徑": "/private",
        "原因": "approved privacy request",
    }
    參數.update(覆寫)
    return 參數


def _服務(*, 前綴: str = "stable", 時間: float = 123.5) -> SQLite遮蔽命令服務:
    """建立具 deterministic server-owned factories 的命令服務。"""
    return SQLite遮蔽命令服務(
        遮蔽識別碼工廠=lambda: f"redaction-{前綴}",
        稽核事件識別碼工廠=lambda: f"audit-{前綴}",
        請求識別碼工廠=lambda: f"request-{前綴}",
        時鐘=lambda: 時間,
    )


def test_server_command與mapping_audit_payload_ledger共用單一commit_point(tmp_path: Path) -> None:
    """整合 seam 由 G04 擁有 transaction；首次與 restart replay 回傳同一不可逆 receipt。"""
    資料庫 = tmp_path / "integrated-command.sqlite3"
    _建立命令資料庫(資料庫)
    遮蔽服務 = SQLite不可逆遮蔽服務(str(資料庫))

    首次 = 遮蔽服務.執行命令(_服務(), **_命令參數())
    assert 首次["redaction_id"] == "redaction-stable"
    assert 首次["audit_event_id"] == "audit-stable"
    assert 首次["actor_id"] == "admin-main"
    assert 首次["redacted_at"] == 123.5

    with sqlite3.connect(資料庫) as 連線:
        payload = json.loads(連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-main'"
        ).fetchone()[0])
        assert payload == {
            "private": {"$tombstone": {"redaction_id": "redaction-stable", "redacted_at": 123.5}}
        }
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (1,)

    def 不得重配():
        raise AssertionError("restart replay 不得重新配置 server identity")

    重啟命令服務 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=不得重配,
        稽核事件識別碼工廠=不得重配,
        請求識別碼工廠=不得重配,
        時鐘=不得重配,
    )
    assert SQLite不可逆遮蔽服務(str(資料庫)).執行命令(
        重啟命令服務, **_命令參數()
    ) == 首次
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (1,)


def test_commit已durable但acknowledgement遺失仍回傳權威receipt且不重做(monkeypatch, tmp_path: Path) -> None:
    """COMMIT 已完成時以 durable graph 為真；普通 acknowledgement error 不可謊報 rollback。"""
    資料庫 = tmp_path / "commit-ack-loss.sqlite3"
    _建立命令資料庫(資料庫)

    class 提交後失聯連線(sqlite3.Connection):
        def commit(self) -> None:
            super().commit()
            raise sqlite3.OperationalError("synthetic acknowledgement loss")

    def 開啟(路徑: str):
        檔案 = os.stat(路徑)
        連線 = sqlite3.connect(
            f"file:{路徑}?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=30,
            factory=提交後失聯連線,
        )
        連線.execute("PRAGMA foreign_keys=ON")
        return 連線, (檔案.st_dev, 檔案.st_ino)

    monkeypatch.setattr(遮蔽模組, "_開啟既有資料庫與釘選", 開啟)
    receipt = SQLite不可逆遮蔽服務(str(資料庫)).執行命令(_服務(), **_命令參數())
    assert receipt["redaction_id"] == "redaction-stable"
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (1,)
        assert "RAW_A20_02" not in 連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-main'"
        ).fetchone()[0]


def test_commit後canonical_path換成相同graph的不同inode仍固定失敗(monkeypatch, tmp_path: Path) -> None:
    """Fresh readback 必須證明原 owner inode；相同內容的 replacement 仍不是同一 commit authority。"""
    資料庫 = tmp_path / "commit-replaced.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _建立命令資料庫(資料庫)

    class 提交後替換連線(sqlite3.Connection):
        def commit(self) -> None:
            super().commit()
            with sqlite3.connect(replacement) as 目標:
                self.backup(目標)
            os.replace(replacement, 資料庫)
            raise sqlite3.OperationalError("synthetic post-commit path replacement")

    def 開啟(路徑: str):
        檔案 = os.stat(路徑)
        連線 = sqlite3.connect(
            f"file:{路徑}?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=30,
            factory=提交後替換連線,
        )
        連線.execute("PRAGMA foreign_keys=ON")
        return 連線, (檔案.st_dev, 檔案.st_ino)

    monkeypatch.setattr(遮蔽模組, "_開啟既有資料庫與釘選", 開啟)
    with pytest.raises(不可逆遮蔽錯誤):
        SQLite不可逆遮蔽服務(str(資料庫)).執行命令(_服務(), **_命令參數())


def test_identity_capture_window不得把外來inode記成owner_authority(monkeypatch, tmp_path: Path) -> None:
    """Path 在 validation/capture 間 ABA 時，foreign exact graph 不得被當作 owner outcome。"""
    資料庫 = tmp_path / "capture-window.sqlite3"
    外來資料庫 = tmp_path / "foreign.sqlite3"
    暫存owner = tmp_path / "owner-hidden.sqlite3"
    _建立命令資料庫(資料庫)
    _建立命令資料庫(外來資料庫)
    SQLite不可逆遮蔽服務(str(外來資料庫)).執行命令(_服務(), **_命令參數())

    原始connect = sqlite3.connect

    class 提交後切換連線(sqlite3.Connection):
        def commit(self) -> None:
            super().commit()
            os.replace(資料庫, 暫存owner)
            os.replace(外來資料庫, 資料庫)
            raise sqlite3.OperationalError("synthetic acknowledgement loss after ABA")

    def 開啟owner(*args, **kwargs):
        kwargs["factory"] = 提交後切換連線
        return 原始connect(*args, **kwargs)

    monkeypatch.setattr(稽核資料庫模組.sqlite3, "connect", 開啟owner)
    with pytest.raises(不可逆遮蔽錯誤):
        SQLite不可逆遮蔽服務(str(資料庫)).執行命令(_服務(), **_命令參數())
    assert not hasattr(遮蔽模組, "_資料庫檔案識別")


@pytest.mark.parametrize("失敗階段", ("mapping", "audit", "payload", "ledger", "commit"))
def test_任一transaction階段失敗都完整rollback且raw不變(monkeypatch, tmp_path: Path, 失敗階段: str) -> None:
    """在五個真 SQL seam 注入普通失敗；mapping/audit/payload/ledger 必須同為零。"""
    資料庫 = tmp_path / f"rollback-{失敗階段}.sqlite3"
    _建立命令資料庫(資料庫)

    class 寫入失敗連線(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            正規SQL = " ".join(sql.split())
            前綴 = {
                "mapping": "INSERT INTO redaction_idempotency_commands",
                "audit": "INSERT INTO audit_events",
                "payload": "UPDATE endpoint_invocations SET input_json=",
                "ledger": "INSERT INTO endpoint_redactions",
            }.get(失敗階段)
            if 前綴 is not None and 正規SQL.startswith(前綴):
                raise sqlite3.OperationalError(f"synthetic {失敗階段} failure")
            return super().execute(sql, parameters)

        def commit(self) -> None:
            if 失敗階段 == "commit":
                raise sqlite3.OperationalError("synthetic pre-commit failure")
            super().commit()

    def 開啟(路徑: str):
        檔案 = os.stat(路徑)
        連線 = sqlite3.connect(
            f"file:{路徑}?mode=rw",
            uri=True,
            isolation_level=None,
            timeout=30,
            factory=寫入失敗連線,
        )
        連線.execute("PRAGMA foreign_keys=ON")
        return 連線, (檔案.st_dev, 檔案.st_ino)

    monkeypatch.setattr(遮蔽模組, "_開啟既有資料庫與釘選", 開啟)
    with pytest.raises(不可逆遮蔽錯誤):
        SQLite不可逆遮蔽服務(str(資料庫)).執行命令(_服務(), **_命令參數())
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-main'"
        ).fetchone() == ('{"private":"RAW_A20_02"}',)
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (0,)


def test_integrated_same_key_different_body保留固定conflict且零新增mutation(tmp_path: Path) -> None:
    """Conflict 由 command authority 原樣穿出；既有 mapping/audit/ledger/tombstone 不變。"""
    資料庫 = tmp_path / "integrated-conflict.sqlite3"
    _建立命令資料庫(資料庫)
    服務 = SQLite不可逆遮蔽服務(str(資料庫))
    首次 = 服務.執行命令(_服務(), **_命令參數())

    with pytest.raises(遮蔽命令冪等衝突) as 錯誤:
        服務.執行命令(_服務(前綴="forbidden"), **_命令參數(原因="different request"))
    assert 錯誤.value.args == ("遮蔽命令冪等衝突",)
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (1,)
        payload = 連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-main'"
        ).fetchone()[0]
        assert "RAW_A20_02" not in payload
        assert 首次["redaction_id"] in payload


def test_integrated_concurrent_retry只提交一組mapping_audit_tombstone_ledger(tmp_path: Path) -> None:
    """四個真正 transaction owners 競爭時，所有 caller 都取得 winner 的完整 receipt。"""
    資料庫 = tmp_path / "integrated-concurrency.sqlite3"
    _建立命令資料庫(資料庫)

    def 執行(索引: int):
        return SQLite不可逆遮蔽服務(str(資料庫)).執行命令(
            _服務(前綴=f"worker-{索引}", 時間=200.0 + 索引),
            **_命令參數(),
        )

    with ThreadPoolExecutor(max_workers=4) as 執行池:
        receipts = list(執行池.map(執行, range(4)))
    assert receipts and all(receipt == receipts[0] for receipt in receipts)
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (1,)
        payload = 連線.execute(
            "SELECT input_json FROM endpoint_invocations WHERE id='invocation-main'"
        ).fetchone()[0]
        assert "RAW_A20_02" not in payload
        assert receipts[0]["redaction_id"] in payload


def test_same_principal_key與canonical_request回放同一server_identity且restart不重配(tmp_path: Path) -> None:
    """Exact retry 只回放同一命令，不新增 mapping，也不再次呼叫 server factories。"""
    資料庫 = tmp_path / "replay.sqlite3"
    _建立命令資料庫(資料庫)
    次數 = {"redaction": 0, "audit": 0, "request": 0, "clock": 0}

    def 配置(名稱: str, 值):
        def 工廠():
            次數[名稱] += 1
            return 值
        return 工廠

    服務 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=配置("redaction", "redaction-stable"),
        稽核事件識別碼工廠=配置("audit", "audit-stable"),
        請求識別碼工廠=配置("request", "request-stable"),
        時鐘=配置("clock", 123.5),
    )
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        首次 = 服務.取得或建立(連線, **_命令參數())
        重試 = 服務.取得或建立(連線, **_命令參數())
        assert 重試 == 首次
        assert 次數 == {"redaction": 1, "audit": 1, "request": 1, "clock": 1}
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        連線.commit()

    def 不得重配():
        raise AssertionError("restart replay 不得重新配置 server identity")

    重啟服務 = SQLite遮蔽命令服務(
        遮蔽識別碼工廠=不得重配,
        稽核事件識別碼工廠=不得重配,
        請求識別碼工廠=不得重配,
        時鐘=不得重配,
    )
    with sqlite3.connect(資料庫) as 重新開啟:
        重新開啟.execute("PRAGMA foreign_keys=ON")
        重新開啟.execute("BEGIN IMMEDIATE")
        assert 重啟服務.取得或建立(重新開啟, **_命令參數()) == 首次
        重新開啟.rollback()


def test_same_principal_key不同canonical_request固定衝突且零mutation(tmp_path: Path) -> None:
    """同一 authority domain 的 key 不可改綁 request，衝突不配置第二組 identity。"""
    資料庫 = tmp_path / "conflict.sqlite3"
    _建立命令資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        原命令 = _服務().取得或建立(連線, **_命令參數())
        原資料 = 連線.execute(f"SELECT * FROM {命令資料表}").fetchall()
        with pytest.raises(遮蔽命令冪等衝突, match="^遮蔽命令冪等衝突$") as 捕捉:
            _服務(前綴="不得使用", 時間=999).取得或建立(
                連線,
                **_命令參數(原因="different approved reason"),
            )
        assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
        assert 連線.execute(f"SELECT * FROM {命令資料表}").fetchall() == 原資料
        assert 原命令.遮蔽識別碼 == "redaction-stable"
        連線.rollback()


def test_same_key改成不存在target仍優先固定衝突而非洩漏target狀態(tmp_path: Path) -> None:
    """已綁定 key 的不同 canonical request 不可由 target existence 改變 failure class。"""
    資料庫 = tmp_path / "conflict-before-target-lookup.sqlite3"
    _建立命令資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        _服務().取得或建立(連線, **_命令參數())
        原資料 = 連線.execute(f"SELECT * FROM {命令資料表}").fetchall()
        with pytest.raises(遮蔽命令冪等衝突, match="^遮蔽命令冪等衝突$"):
            _服務(前綴="不得使用").取得或建立(
                連線,
                **_命令參數(
                    呼叫識別碼="invocation-missing",
                    目標列識別碼="invocation-missing",
                ),
            )
        assert 連線.execute(f"SELECT * FROM {命令資料表}").fetchall() == 原資料
        連線.rollback()


def test_mapping只加入caller_transaction且rollback不留row(tmp_path: Path) -> None:
    """A20-01 不自持 commit；mapping 可在 A20-02 與 audit/tombstone/ledger 共用 commit point。"""
    資料庫 = tmp_path / "caller-transaction.sqlite3"
    _建立命令資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(遮蔽命令錯誤, match="^遮蔽命令無法建立$"):
            _服務().取得或建立(連線, **_命令參數())
        連線.execute("BEGIN IMMEDIATE")
        _服務().取得或建立(連線, **_命令參數())
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        連線.rollback()
    with sqlite3.connect(資料庫) as 重新開啟:
        assert 重新開啟.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (0,)


def test_principal加key是authority_domain且並行replay只有一筆mapping(tmp_path: Path) -> None:
    """一般 deferred caller transactions 的 concurrent retry 仍只配置並保存一個命令。"""
    資料庫 = tmp_path / "concurrency.sqlite3"
    _建立命令資料庫(資料庫)
    交易閘門 = Barrier(2)
    factory閘門 = Barrier(2)
    次數鎖 = Lock()
    factory次數 = 0

    def 建立(索引: int):
        def 配置(值, *, 同步: bool = False):
            def 工廠():
                nonlocal factory次數
                with 次數鎖:
                    factory次數 += 1
                if 同步:
                    try:
                        factory閘門.wait(timeout=0.5)
                    except BrokenBarrierError:
                        pass
                return 值
            return 工廠

        with sqlite3.connect(資料庫, timeout=10) as 連線:
            連線.execute("PRAGMA foreign_keys=ON")
            連線.execute("BEGIN")
            交易閘門.wait(timeout=2)
            服務 = SQLite遮蔽命令服務(
                遮蔽識別碼工廠=配置(f"redaction-worker-{索引}", 同步=True),
                稽核事件識別碼工廠=配置(f"audit-worker-{索引}"),
                請求識別碼工廠=配置(f"request-worker-{索引}"),
                時鐘=配置(123.5),
            )
            結果 = 服務.取得或建立(連線, **_命令參數())
            連線.commit()
            return 結果

    with ThreadPoolExecutor(max_workers=2) as 執行池:
        結果們 = list(執行池.map(建立, range(2)))
    assert 結果們[0] == 結果們[1]
    assert factory次數 == 4
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (1,)
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        另一管理員 = _服務(前綴="other-admin").取得或建立(
            連線,
            **_命令參數(管理員識別碼="admin-other"),
        )
        assert 另一管理員.遮蔽識別碼 == "redaction-other-admin"
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (2,)
        連線.rollback()


def test_mapping跟隨既有五年invocation保存政策且不阻斷purge(tmp_path: Path) -> None:
    """到期 invocation 清除時 mapping 同交易移除，不改既有候選、期限或其他治理資料政策。"""
    資料庫 = tmp_path / "retention.sqlite3"
    _建立命令資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        _服務().取得或建立(連線, **_命令參數())
        連線.commit()

    結果 = SQLite保存清除服務(str(資料庫)).清除(200_000_000)
    assert 結果.呼叫數 == 1
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM endpoint_invocations").fetchone() == (0,)


def test_public_command_seam拒絕client_controlled_authority_identity_time與不安全key(tmp_path: Path) -> None:
    """公開方法不接受 admin flag、internal IDs、timestamp、original value/hash。"""
    參數名稱 = set(signature(SQLite遮蔽命令服務.取得或建立).parameters)
    整合參數名稱 = set(signature(SQLite不可逆遮蔽服務.執行命令).parameters)
    for 禁止 in (
        "管理員授權", "is_admin", "遮蔽識別碼", "稽核事件識別碼", "請求識別碼",
        "發生時間", "redacted_at", "original_value", "original_sha256", "payload",
    ):
        assert 禁止 not in 參數名稱
        assert 禁止 not in 整合參數名稱

    資料庫 = tmp_path / "validation.sqlite3"
    _建立命令資料庫(資料庫)
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        for 壞鍵 in ("", "x" * 129, "has space", "含中文"):
            with pytest.raises(遮蔽命令錯誤, match="^遮蔽命令無法建立$"):
                _服務().取得或建立(連線, **_命令參數(冪等鍵=壞鍵))
        原值標記 = "SYNTHETIC_ORIGINAL_VALUE_MUST_NOT_PERSIST_A20_01"
        惡意參數 = _命令參數()
        惡意參數["original_value"] = 原值標記
        with pytest.raises(TypeError) as 捕捉:
            _服務().取得或建立(連線, **惡意參數)
        assert 原值標記 not in str(捕捉.value)
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (0,)
        連線.rollback()
    assert 原值標記.encode() not in 資料庫.read_bytes()


@pytest.mark.parametrize("語意錯誤", [
    遮蔽命令冪等衝突("spoof"),
    遮蔽命令目標不存在("spoof"),
    遮蔽路徑無效("spoof"),
])
@pytest.mark.parametrize("工廠名稱", [
    "遮蔽識別碼工廠", "稽核事件識別碼工廠", "請求識別碼工廠", "時鐘",
])
def test_server_factory_raise_same_semantic_exception仍正規化為命令錯誤(
    tmp_path: Path, 語意錯誤: BaseException, 工廠名稱: str,
) -> None:
    資料庫 = tmp_path / f"factory-spoof-{工廠名稱}.sqlite3"
    _建立命令資料庫(資料庫)

    def spoof():
        raise 語意錯誤

    factories = {
        "遮蔽識別碼工廠": lambda: "redaction-safe",
        "稽核事件識別碼工廠": lambda: "audit-safe",
        "請求識別碼工廠": lambda: "request-safe",
        "時鐘": lambda: 123.5,
    }
    factories[工廠名稱] = spoof
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("BEGIN IMMEDIATE")
        with pytest.raises(遮蔽命令錯誤, match="^遮蔽命令無法建立$"):
            SQLite遮蔽命令服務(**factories).取得或建立(連線, **_命令參數())
        assert 連線.execute(f"SELECT count(*) FROM {命令資料表}").fetchone() == (0,)
        連線.rollback()
